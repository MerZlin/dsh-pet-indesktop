"""Build fresh, non-release HUMAN acceptance Cores with REAL feature boundaries.

An ephemeral signing key is generated in memory and discarded after assembling
three normal packages. Only its public half enters owned build snapshots. Never
change repository trust policy, user configuration or system startup settings.
"""

from __future__ import annotations

import argparse
import time
import zipfile
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from .build_feature_management_delivery import sign_version
from .build_screen_delivery import ROOT, assemble_package, build_core, digest, verify_worker_inputs, write_json


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
    key = Ed25519PrivateKey.generate()
    public = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex()
    packages = {}
    for name, version in (("v1", "1.0.0"), ("v2", "1.0.1"), ("v3", "1.0.2")):
        package = assemble_package(ROOT, output / "packages" / name, worker, key, synthetic=False)
        sign_version(package, key, version)
        packages[name] = str(package)
    del key
    archives = {}
    for name, package_path in packages.items():
        archive = output / "packages" / f"{name}.zip"
        with zipfile.ZipFile(archive, "x", compression=zipfile.ZIP_DEFLATED) as target:
            package = Path(package_path)
            for file in sorted(package.rglob("*")):
                if file.is_file():
                    target.write(file, file.relative_to(package).as_posix())
        archives[name] = {"path": str(archive), "sha256": digest(archive.read_bytes()), "bytes": archive.stat().st_size}
    artifacts = {
        "manual_acceptance_only": True,
        "release": False,
        "synthetic_boundary": False,
        "system_startup_registration": "excluded",
        "public_key": public,
        "probe_manifest_sha256": args.probe_manifest_sha256,
        "packages": packages,
        "archives": archives,
        "cores": {},
    }
    write_json(output / "manual-artifacts.json", artifacts)
    (output / "HUMAN-ACCEPTANCE-ONLY.txt").write_text(
        "人工验收构建，不是正式发布。临时公钥，仅用于同目录三个签名版本。\n"
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
            public_key=public,
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
