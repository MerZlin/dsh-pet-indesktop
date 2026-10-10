"""Validate and compile the current Core WebM Inno Setup package.

This is the canonical build wrapper for packaging/core_webm.iss. The wrapper
validates both bounded manifests before invoking ISCC; the ISS script itself
additionally checks the two ZIP files exist at preprocessing time.
"""

from __future__ import annotations

import argparse
import subprocess
import tempfile
from pathlib import Path

try:
    from .validate_phase5a_setup import validate_official_package_inputs
except ImportError:  # direct python scripts/build_core_webm_setup.py
    from validate_phase5a_setup import validate_official_package_inputs

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SCRIPT = ROOT / "packaging/core_webm.iss"
DEFAULT_ISCC = Path("E:/tools/InnoSetup6/ISCC.exe")
PORTABLE_MARKER_TEXT = '{"data":"data","format_version":1,"product_id":"dsh-pet-core-webm"}'


def build_setup(
    *,
    core_dir: Path,
    package_dir: Path,
    core_output_dir: Path,
    core_version: str,
    iscc: Path = DEFAULT_ISCC,
    script: Path = DEFAULT_SCRIPT,
) -> int:
    """Validate inputs and return ISCC's exit code without running the installer."""

    core = Path(core_dir).resolve(strict=True)
    packages = Path(package_dir).resolve(strict=True)
    output = Path(core_output_dir).resolve()
    script_path = Path(script).resolve(strict=True)
    compiler = Path(iscc).resolve(strict=True)
    validate_official_package_inputs(packages)
    if not core.is_dir():
        raise ValueError(f"CoreDir is not a directory: {core}")
    if not core_version:
        raise ValueError("CoreVersion must not be empty")
    output.mkdir(parents=True, exist_ok=True)
    # The marker is a Setup-owned input, not part of the audited Core source
    # tree.  Keep it in a short-lived sibling directory so the copy step can
    # embed it without mutating the Core build output.
    with tempfile.TemporaryDirectory(prefix="portable-marker-", dir=str(output.parent)) as marker_dir:
        marker_path = Path(marker_dir) / "portable.json"
        with marker_path.open("w", encoding="utf-8", newline="") as stream:
            stream.write(PORTABLE_MARKER_TEXT)
        command = [
            str(compiler),
            "/Qp",
            f"/DCoreDir={core}",
            f"/DPackageDir={packages}",
            f"/DCoreOutputDir={output}",
            f"/DCoreVersion={core_version}",
            f"/DPortableMarker={marker_path}",
            str(script_path),
        ]
        completed = subprocess.run(command, cwd=ROOT, check=False)
        return int(completed.returncode)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--core-dir", required=True, type=Path)
    parser.add_argument("--package-dir", required=True, type=Path)
    parser.add_argument("--core-output-dir", required=True, type=Path)
    parser.add_argument("--core-version", required=True)
    parser.add_argument("--iscc", type=Path, default=DEFAULT_ISCC)
    parser.add_argument("--script", type=Path, default=DEFAULT_SCRIPT)
    args = parser.parse_args(argv)
    try:
        return build_setup(
            core_dir=args.core_dir,
            package_dir=args.package_dir,
            core_output_dir=args.core_output_dir,
            core_version=args.core_version,
            iscc=args.iscc,
            script=args.script,
        )
    except (OSError, TypeError, ValueError) as exc:
        print(f"PHASE5A_SETUP_BUILD_BLOCKED: {exc}")
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
