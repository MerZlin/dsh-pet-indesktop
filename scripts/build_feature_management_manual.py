"""Build fresh, non-release HUMAN acceptance Cores with REAL feature boundaries.

Ordinary local-activation packages are assembled without publisher keys or
signatures: an AI package plus screen version fixtures and fixed ZIP names.
Core validates their closed manifest/file inventory and compatibility only.
Never change repository trust policy, user configuration or system startup
settings.
"""

from __future__ import annotations

import argparse
import json
import shutil
import time
import zipfile
from pathlib import Path

from .build_screen_delivery import (
    ROOT,
    assemble_ai_package,
    assemble_package,
    build_core,
    digest,
    verify_worker_inputs,
    write_json,
)


def publish_canonical_archive(source: Path, output_dir: Path) -> dict[str, object]:
    """Publish the fixed official ZIP name consumed by the closed Setup intent."""
    source = Path(source)
    output_dir = Path(output_dir)
    if source.name != "v1.zip" or not source.is_file():
        raise ValueError("manual_canonical_archive_source_invalid")
    output_dir.mkdir(parents=True, exist_ok=True)
    destination = output_dir / "official.screen-understanding.zip"
    if destination.exists():
        raise FileExistsError(destination)
    with source.open("rb") as input_file, destination.open("xb") as output_file:
        shutil.copyfileobj(input_file, output_file)
    return {
        "feature_id": "official.screen-understanding",
        "source": "v1",
        "path": str(destination),
        "sha256": digest(destination.read_bytes()),
        "bytes": destination.stat().st_size,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--worker-build", type=Path, required=True)
    parser.add_argument("--probe-bundle", type=Path, required=True)
    parser.add_argument("--probe-manifest-sha256", required=True)
    args = parser.parse_args(argv)
    from pet.feature_probe_windows import TrustedProbeBundle

    started = time.perf_counter()
    TrustedProbeBundle(args.probe_bundle, args.probe_manifest_sha256).verify()
    # Reject an automated synthetic Worker before creating any output directory.
    worker = verify_worker_inputs(ROOT, args.worker_build, synthetic=False)
    output = args.output.absolute()
    output.mkdir(parents=True, exist_ok=False)
    packages = {}
    for name, version in (("v1", "1.0.0"), ("v2", "1.0.1"), ("v3", "1.0.2")):
        package = assemble_package(ROOT, output / "packages" / name, worker, synthetic=False)
        manifest = json.loads((package / "manifest.json").read_text(encoding="utf-8"))
        manifest["version"] = version
        manifest["files"] = {
            file.relative_to(package).as_posix(): {"sha256": digest(file.read_bytes()), "size": file.stat().st_size}
            for file in sorted(package.rglob("*"))
            if file.is_file() and file.name != "manifest.json"
        }
        (package / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        packages[name] = str(package)
    ai_package = assemble_ai_package(ROOT, output / "packages" / "ai-chat-v1", version="1.0.1")
    packages["official.ai-chat"] = str(ai_package)
    archives = {}
    ai_archive = output / "packages" / "official.ai-chat.zip"
    with zipfile.ZipFile(ai_archive, "x", compression=zipfile.ZIP_DEFLATED) as target:
        for file in sorted(ai_package.rglob("*")):
            if file.is_file():
                target.write(file, file.relative_to(ai_package).as_posix())
    archives["official.ai-chat"] = {
        "path": str(ai_archive),
        "sha256": digest(ai_archive.read_bytes()),
        "bytes": ai_archive.stat().st_size,
    }
    for name, package_path in packages.items():
        if name == "official.ai-chat":
            continue
        archive = output / "packages" / f"{name}.zip"
        with zipfile.ZipFile(archive, "x", compression=zipfile.ZIP_DEFLATED) as target:
            package = Path(package_path)
            for file in sorted(package.rglob("*")):
                if file.is_file():
                    target.write(file, file.relative_to(package).as_posix())
        archives[name] = {"path": str(archive), "sha256": digest(archive.read_bytes()), "bytes": archive.stat().st_size}
    archives["official.screen-understanding"] = publish_canonical_archive(output / "packages/v1.zip", output / "packages")
    artifacts = {
        "manual_acceptance_only": True,
        "release": False,
        "synthetic_boundary": False,
        "system_startup_registration": "excluded",
        "package_activation": "local-structure",
        "signature_required": False,
        "probe_manifest_sha256": args.probe_manifest_sha256,
        "packages": packages,
        "archives": archives,
        "cores": {},
    }
    write_json(output / "manual-artifacts.json", artifacts)
    (output / "HUMAN-ACCEPTANCE-ONLY.txt").write_text(
        "人工验收构建，不是正式发布。Phase5A 使用本地结构/完整性校验，不需要公钥私钥。\n"
        "真实识屏、网络、Windows安全存储；只在你点击或启用自动识屏后执行。\n"
        "配置须使用本次E盘独立APPDATA；不得替换已有配置。开机自启不在验收范围，禁止写系统自启项。\n"
        "凭据只输入本地设置界面，不发给Codex。不得把本构建作为正式分发。\n",
        encoding="utf-8",
    )
    for chat in (False, True):
        name = "chat" if chat else "no-chat"
        executable = build_core(
            ROOT,
            output / name,
            chat=chat,
            entrypoint=ROOT / "packaging/phase4b_manual_entry.py",
            probe_bundle=args.probe_bundle,
            probe_manifest_sha256=args.probe_manifest_sha256,
        )
        artifacts["cores"][name] = str(executable)
        artifacts["build_elapsed_seconds"] = round(time.perf_counter() - started, 3)
        write_json(output / "manual-artifacts.json", artifacts)
    print(f"HUMAN ACCEPTANCE ONLY: {output / 'manual-artifacts.json'}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
