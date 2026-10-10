"""Validate the official ZIP inputs before compiling the Core WebM Setup.

The Inno Setup preprocessor can prove that both named ZIPs exist, but it cannot
parse a ZIP manifest.  This bounded preflight closes that gap without importing
or executing package code: it routes only the root manifest, then compares the
manifest registration with the explicit official build registration.
"""

from __future__ import annotations

import argparse
import json
import zipfile
from pathlib import Path
from typing import Mapping

from pet.local_package_intents import LocalPackageRoute, route_local_package
from pet.official_features import official_feature

EXPECTED_PACKAGE_FORMATS = {
    "official.ai-chat": 2,
    "official.screen-understanding": 1,
}
EXPECTED_PACKAGE_FILES = tuple(EXPECTED_PACKAGE_FORMATS)


class SetupPackageValidationError(ValueError):
    """Raised when the Setup package directory is not an official input set."""


def _official_manifest_checks(owner: str, route: LocalPackageRoute) -> list[str]:
    registration = official_feature(owner)
    problems: list[str] = []
    if route.feature_id != registration.id:
        problems.append(f"id={route.feature_id!r} expected {registration.id!r}")
    if route.factory != registration.factory:
        problems.append(f"factory={route.factory!r} expected {registration.factory!r}")
    if route.execution_kind != registration.execution_kind:
        problems.append(f"execution_kind={route.execution_kind!r} expected {registration.execution_kind!r}")
    if route.format_version != EXPECTED_PACKAGE_FORMATS[owner]:
        problems.append(f"format_version={route.format_version!r} expected {EXPECTED_PACKAGE_FORMATS[owner]!r}")
    if not set(route.capabilities) <= registration.capabilities:
        problems.append("capabilities exceed the official registration")
    return problems


def validate_official_package_inputs(package_dir: Path) -> dict[str, LocalPackageRoute]:
    """Validate and return bounded routes for the two embedded official ZIPs."""

    root = Path(package_dir)
    if not root.is_absolute():
        raise SetupPackageValidationError("PackageDir must be absolute")
    if not root.is_dir():
        raise SetupPackageValidationError(f"PackageDir is not a directory: {root}")
    routes: dict[str, LocalPackageRoute] = {}
    for owner in EXPECTED_PACKAGE_FILES:
        source = root / f"{owner}.zip"
        if not source.is_file() or source.is_symlink():
            raise SetupPackageValidationError(f"missing official package: {source.name}")
        try:
            route = route_local_package(source)
        except (OSError, RuntimeError, TypeError, ValueError, zipfile.BadZipFile) as exc:
            raise SetupPackageValidationError(f"invalid {source.name}: {exc}") from exc
        problems = _official_manifest_checks(owner, route)
        if problems:
            raise SetupPackageValidationError(f"{source.name}: " + "; ".join(problems))
        routes[owner] = route
    return routes


def _route_summary(route: LocalPackageRoute) -> Mapping[str, object]:
    return {
        "id": route.feature_id,
        "factory": route.factory,
        "execution_kind": route.execution_kind,
        "format_version": route.format_version,
        "version": route.version,
        "manifest_sha256": route.manifest_digest,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("package_dir", type=Path)
    args = parser.parse_args(argv)
    try:
        routes = validate_official_package_inputs(args.package_dir.resolve())
    except (OSError, SetupPackageValidationError, TypeError, ValueError) as exc:
        print(f"PHASE5A_SETUP_INPUT_INVALID: {exc}")
        return 3
    print(json.dumps({owner: _route_summary(route) for owner, route in routes.items()}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
