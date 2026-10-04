"""Build TWO new, explicitly marked Phase4B production-path validation Cores.

Private signing key exists only in this process. It is never serialized. These
are not release artifacts. Trust policy is written only to owned source snapshots.
"""

from __future__ import annotations

import argparse
import json
import zipfile
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

if __package__:
    from .build_screen_delivery import ROOT, assemble_package, build_core, digest, verify_worker_inputs, write_json
else:
    from build_screen_delivery import ROOT, assemble_package, build_core, digest, verify_worker_inputs, write_json


def sign_version(package: Path, key, version: str, *, fault: str | None = None):
    """Own generated fixture only; preserve a closed authenticated inventory."""
    if fault is not None:
        factory = package / "host/factory.py"
        source = factory.read_text(encoding="utf-8")
        if fault == "self-check":
            source = source.replace("def create_host():", 'def create_host():\n    raise RuntimeError("validation-only self-check failure")')
        elif fault == "startup":
            source = source.replace(
                "def create_host():",
                'def create_host():\n    import importlib.util\n    if importlib.util.find_spec("PySide6") is not None:\n        raise RuntimeError("validation-only production-load failure")',
            )
        else:
            raise ValueError("unknown fixture fault")
        factory.write_text(source, encoding="utf-8")
    manifest = json.loads((package / "manifest.json").read_bytes())
    manifest["version"] = version
    manifest["files"] = {
        p.relative_to(package).as_posix(): {"sha256": digest(p.read_bytes()), "size": p.stat().st_size}
        for p in sorted(package.rglob("*"))
        if p.is_file() and p.name not in ("manifest.json", "manifest.sig")
    }
    raw = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()
    (package / "manifest.json").write_bytes(raw)
    (package / "manifest.sig").write_bytes(key.sign(raw))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--worker-build", type=Path, required=True)
    parser.add_argument("--probe-bundle", type=Path, required=True)
    parser.add_argument("--probe-manifest-sha256", required=True)
    args = parser.parse_args(argv)
    from pet.feature_probe_windows import TrustedProbeBundle

    TrustedProbeBundle(args.probe_bundle, args.probe_manifest_sha256).verify()
    worker = verify_worker_inputs(ROOT, args.worker_build, synthetic=True)
    output = args.output.absolute()
    output.mkdir(parents=True, exist_ok=False)
    key = Ed25519PrivateKey.generate()
    public = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex()
    packages = {}
    for name, version, fault in (
        ("v1", "1.0.0", None),
        ("v2", "1.0.1", None),
        ("v3", "1.0.2", None),
        ("bad-probe", "1.0.3", "self-check"),
        ("bad-startup", "1.0.4", "startup"),
    ):
        package = assemble_package(ROOT, output / "packages" / name, worker, key, synthetic=True)
        sign_version(package, key, version, fault=fault)
        packages[name] = str(package)
    del key
    archive = output / "packages/v1.zip"
    with zipfile.ZipFile(archive, "x", compression=zipfile.ZIP_DEFLATED) as target:
        for file in sorted(Path(packages["v1"]).rglob("*")):
            if file.is_file():
                target.write(file, file.relative_to(packages["v1"]).as_posix())
    cores = {}
    for chat in (False, True):
        name = "chat" if chat else "no-chat"
        executable = build_core(
            ROOT,
            output / name,
            chat=chat,
            public_key=public,
            entrypoint=ROOT / "packaging/phase4b_validation_entry.py",
            probe_bundle=args.probe_bundle,
            probe_manifest_sha256=args.probe_manifest_sha256,
        )
        cores[name] = str(executable)
        write_json(
            output / "validation-artifacts.json",
            {
                "validation_only": True,
                "public_key": public,
                "probe_manifest_sha256": args.probe_manifest_sha256,
                "packages": packages,
                "zip": str(archive),
                "cores": cores,
            },
        )
        print(json.dumps({"event": "core_built", "variant": name, "executable": str(executable)}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
