"""Build an owned onedir permission canary bundle; no source fallback at run."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

# This module is also loaded by direct script execution from `scripts/` during
# the headless Worker build. Keep the repository package importable regardless
# of the caller's current working directory or `sys.path[0]`.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

SODIUM_ARCHIVE_SHA256 = "3e03a726fac4bc09cb61d8f29d658ef7a5eca0811de59082130414f7ca2e4279"
SODIUM_DLL_SHA256 = "f656aeb789bfc3a2ac587fae1d7cfe278bbe8ce73f28da73b4d08282ea8f9fe3"


def stage_headless_crypto(archive_path: Path, output: Path) -> Path:
    """Pinned official x64 Release libsodium; no CFFI, user installation or ACL."""
    if hashlib.sha256(archive_path.read_bytes()).hexdigest() != SODIUM_ARCHIVE_SHA256:
        raise ValueError("crypto archive digest")
    output.mkdir(parents=True, exist_ok=False)
    with zipfile.ZipFile(archive_path) as archive:
        dll = archive.read("libsodium/x64/Release/v143/dynamic/libsodium.dll")
        if hashlib.sha256(dll).hexdigest() != SODIUM_DLL_SHA256:
            raise ValueError("crypto DLL digest")
        (output / "libsodium.dll").write_bytes(dll)
        license = Path(__file__).resolve().parents[1] / "packaging/licenses/LIBSODIUM-LICENSE.txt"
        if hashlib.sha256(license.read_bytes()).hexdigest() != "508a76d186356c0dd807a670ef510964f8724557024796a2c426c6c0e19ab683":
            raise ValueError("libsodium license digest")
        (output / "LICENSE-0.txt").write_bytes(license.read_bytes())
    return output


def libsodium_activation_resource_policy(raw: bytes) -> dict:
    root = ET.fromstring(raw)
    expected = b"<?xml version='1.0' encoding='UTF-8' standalone='yes'?>\r\n<assembly xmlns='urn:schemas-microsoft-com:asm.v1' manifestVersion='1.0'>\r\n  <trustInfo xmlns=\"urn:schemas-microsoft-com:asm.v3\">\r\n    <security>\r\n      <requestedPrivileges>\r\n        <requestedExecutionLevel level='asInvoker' uiAccess='false' />\r\n      </requestedPrivileges>\r\n    </security>\r\n  </trustInfo>\r\n</assembly>\r\n"
    if raw != expected or root.tag != "{urn:schemas-microsoft-com:asm.v1}assembly":
        raise ValueError("unexpected libsodium activation template")
    return {
        "manifest_xml": raw.decode(),
        "manifest_sha256": hashlib.sha256(raw).hexdigest(),
        "resource_removed": True,
        "change": "remove exact upstream libsodium DLL activation resource from owned headless COPY",
    }


def empty_activation_resource_policy(raw: bytes) -> dict:
    if len(raw) > 4096 or b"<!" in raw:
        raise ValueError("third party manifest bound")
    root = ET.fromstring(raw)
    if (
        root.tag != "{urn:schemas-microsoft-com:asm.v1}assembly"
        or set(root.attrib) != {"manifestVersion"}
        or root.get("manifestVersion") != "1.0"
        or len(root)
        or (root.text or "").strip()
    ):
        raise ValueError("unexpected third party activation manifest")
    return {
        "manifest_sha256": hashlib.sha256(raw).hexdigest(),
        "manifest_xml": raw.decode("utf-8"),
        "resource_removed": True,
        "change": "remove exact EMPTY activation resource from OWNED headless dependency copy",
    }


def minimal_runtime_manifest(raw: bytes) -> bytes:
    """Keep CPython compatibility metadata; remove its unused GUI SxS binding."""
    if len(raw) > 64 * 1024 or b"<!DOCTYPE" in raw or b"<!ENTITY" in raw:
        raise ValueError("runtime manifest bound")
    namespace = "urn:schemas-microsoft-com:asm.v1"
    ET.register_namespace("", namespace)
    root = ET.fromstring(raw)
    if root.tag != "{" + namespace + "}assembly":
        raise ValueError("runtime manifest root")
    dependencies = list(root.findall("{" + namespace + "}dependency"))
    if len(dependencies) != 1:
        raise ValueError("unexpected runtime dependency count")
    for dependency in dependencies:
        identities = dependency.findall(".//{" + namespace + "}assemblyIdentity")
        if len(identities) != 1 or identities[0].get("name") != "Microsoft.Windows.Common-Controls":
            raise ValueError("unexpected runtime dependency")
        root.remove(dependency)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def runtime_manifest_removal_policy(raw: bytes) -> dict:
    # Removing the dependency alone still invokes SxS on DLL load. The whole
    # activation resource must be removed from this HEADLESS runtime COPY.
    # Validate the expected upstream template; preserve it in build evidence.
    minimal_runtime_manifest(raw)
    return {
        "manifest_sha256": hashlib.sha256(raw).hexdigest(),
        "manifest_xml": raw.decode("utf-8"),
        "resource_removed": True,
        "change": "remove whole CPython DLL activation resource from owned headless probe copy only",
    }


def owned_runtime_kind(path: Path, *, python_root: Path = Path(sys.base_prefix)) -> str | None:
    """Closed CPython origin/hash proof, not a growing filename exception list."""
    origin = python_root / (path.name if path.name in {"python311.dll", "python3.dll"} else "DLLs/" + path.name)
    if (
        (path.name in {"python311.dll", "python3.dll"} or path.stem in sys.stdlib_module_names)
        and origin.is_file()
        and not origin.is_symlink()
        and hashlib.sha256(origin.read_bytes()).digest() == hashlib.sha256(path.read_bytes()).digest()
    ):
        return "cpython"
    if path.name == "libsodium.dll" and hashlib.sha256(path.read_bytes()).hexdigest() == SODIUM_DLL_SHA256:
        return "headless_crypto"
    return None


def patch_owned_python_runtime(path: Path, *, owned_root: Path) -> dict:
    """Update resources on the newly built OWNED copy, never installed Python."""
    import ctypes as C
    from ctypes import wintypes as W

    import pefile

    path = path.absolute()
    owned_root = owned_root.absolute()
    if not path.is_relative_to(owned_root) or path.resolve().is_relative_to(Path(sys.base_prefix).resolve()):
        raise ValueError("runtime outside owned probe root")
    component_kind = owned_runtime_kind(path)
    if component_kind is None:
        raise ValueError("unexpected runtime component")
    for ancestor in (path, *path.parents):
        if ancestor.is_symlink() or getattr(ancestor.lstat(), "st_file_attributes", 0) & 0x400:
            raise ValueError("runtime copy link")
    if path.is_symlink() or path.stat().st_nlink != 1:
        raise ValueError("runtime copy must be singly owned")
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    pe = pefile.PE(str(path))
    manifests = []
    for kind in pe.DIRECTORY_ENTRY_RESOURCE.entries:
        if kind.id == 24:
            for name in kind.directory.entries:
                for language in name.directory.entries:
                    descriptor = language.data.struct
                    manifests.append((name.id, language.id, pe.get_data(descriptor.OffsetToData, descriptor.Size)))
    pe.close()
    if len(manifests) != 1 or manifests[0][0] != 2:
        raise ValueError("unexpected CPython runtime resources")
    name, language, raw = manifests[0]
    policy = libsodium_activation_resource_policy(raw) if component_kind == "headless_crypto" else runtime_manifest_removal_policy(raw)
    kernel = C.WinDLL("kernel32", use_last_error=True)
    kernel.BeginUpdateResourceW.argtypes = [W.LPCWSTR, W.BOOL]
    kernel.BeginUpdateResourceW.restype = W.HANDLE
    kernel.UpdateResourceW.argtypes = [W.HANDLE, C.c_void_p, C.c_void_p, W.WORD, C.c_void_p, W.DWORD]
    kernel.UpdateResourceW.restype = W.BOOL
    kernel.EndUpdateResourceW.argtypes = [W.HANDLE, W.BOOL]
    kernel.EndUpdateResourceW.restype = W.BOOL
    update = kernel.BeginUpdateResourceW(str(path), False)
    if not update:
        raise C.WinError(C.get_last_error())
    if not kernel.UpdateResourceW(update, C.c_void_p(24), C.c_void_p(name), language, None, 0):
        error = C.get_last_error()
        kernel.EndUpdateResourceW(update, True)
        raise C.WinError(error)
    if not kernel.EndUpdateResourceW(update, False):
        raise C.WinError(C.get_last_error())
    return {
        "before_sha256": before,
        "after_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        **policy,
    }


def write_owned_executable_manifest(path: Path, *, owned_root: Path) -> None:
    """Opt this owned PE into long paths, without activating GUI assemblies.

    Resource editing must precede appending the CArchive overlay: native PE
    editors may discard an existing overlay. Never edit the installed loader.
    """
    import ctypes as C
    import ctypes.wintypes as W

    from pet.feature_state_io import safe_path

    path, owned_root = path.absolute(), owned_root.absolute()
    if not path.is_relative_to(owned_root):
        raise ValueError("executable outside owned build root")
    safe_path(path)
    if path.stat().st_nlink != 1:
        raise ValueError("executable copy must be singly owned")
    raw = (
        b'<assembly xmlns="urn:schemas-microsoft-com:asm.v1" manifestVersion="1.0">'
        b'<application xmlns="urn:schemas-microsoft-com:asm.v3"><windowsSettings>'
        b'<longPathAware xmlns="http://schemas.microsoft.com/SMI/2016/WindowsSettings">true</longPathAware>'
        b"</windowsSettings></application></assembly>"
    )
    kernel = C.WinDLL("kernel32", use_last_error=True)
    kernel.BeginUpdateResourceW.argtypes = [W.LPCWSTR, W.BOOL]
    kernel.BeginUpdateResourceW.restype = W.HANDLE
    kernel.UpdateResourceW.argtypes = [W.HANDLE, C.c_void_p, C.c_void_p, W.WORD, C.c_void_p, W.DWORD]
    kernel.UpdateResourceW.restype = W.BOOL
    kernel.EndUpdateResourceW.argtypes = [W.HANDLE, W.BOOL]
    kernel.EndUpdateResourceW.restype = W.BOOL
    update = kernel.BeginUpdateResourceW(str(path), False)
    if not update:
        raise C.WinError(C.get_last_error())
    buffer = C.create_string_buffer(raw)
    if not kernel.UpdateResourceW(update, C.c_void_p(24), C.c_void_p(1), 0, buffer, len(raw)):
        error = C.get_last_error()
        kernel.EndUpdateResourceW(update, True)
        raise C.WinError(error)
    if not kernel.EndUpdateResourceW(update, False):
        raise C.WinError(C.get_last_error())


def finalize_owned_headless_runtime(bundle: Path, exe: Path, bootloader: Path) -> None:
    """Shared build-only adaptation for a new OWNED helper/Worker, not Core."""
    import pefile
    from PyInstaller.archive.readers import CArchiveReader

    archive = CArchiveReader(str(exe))
    payload = exe.read_bytes()[archive._start_offset :]
    exe.write_bytes(bootloader.read_bytes())
    write_owned_executable_manifest(exe, owned_root=bundle)
    with exe.open("ab") as target:
        target.write(payload)
    # Check that resource editing did not corrupt the executable archive.
    CArchiveReader(str(exe))
    runtime_patch = []
    for runtime in sorted((bundle / "_internal").rglob("*")):
        if (
            runtime.is_file()
            and (runtime.name in {"python311.dll", "python3.dll", "libsodium.dll"} or runtime.suffix == ".pyd")
            and owned_runtime_kind(runtime) is not None
        ):
            pe = pefile.PE(str(runtime))
            resources = getattr(pe, "DIRECTORY_ENTRY_RESOURCE", None)
            has_manifest = resources is not None and any(kind.id == 24 for kind in resources.entries)
            pe.close()
            if has_manifest:
                runtime_patch.append({"path": runtime.relative_to(bundle).as_posix(), **patch_owned_python_runtime(runtime, owned_root=bundle)})
    (bundle / "runtime-patch.json").write_text(json.dumps(runtime_patch, sort_keys=True, indent=2), encoding="utf-8")
    license = bootloader.parent / "source/COPYING.txt"
    if not license.is_file():
        raise ValueError("bootloader license required")
    (bundle / "PYINSTALLER-COPYING.txt").write_bytes(license.read_bytes())
    python_license = Path(sys.base_prefix) / "LICENSE.txt"
    if not python_license.is_file():
        raise ValueError("CPython license required")
    (bundle / "PYTHON-LICENSE.txt").write_bytes(python_license.read_bytes())


HEADLESS_EXCLUDED_MODULES = (
    "features",  # DLC business is loaded only from the verified installed copy.
    "PySide6",
    "PyQt5",
    "PyQt6",
    "numpy",
    "PIL",
    "pet.app",
    "keyring",
    "ctypes",
    "_ctypes",
    # PyInstaller's multiprocessing runtime hook imports socket and calls
    # WSAStartup before the headless probe entrypoint. LPAC intentionally
    # denies network initialization, so this unused module would prevent
    # the verifier from starting even for host-only packages.
    "multiprocessing",
    "cryptography",
    "pet.feature_install_state",
    "pet.feature_version_lease",
    "pet.feature_package_transactions",
)


def headless_crypto_arguments() -> list[str]:
    return ["--exclude-module", "nacl", "--exclude-module", "cffi", "--exclude-module", "_cffi_backend"]


def build(output: Path, *, bootloader: Path, native_extension: Path, crypto_library: Path) -> tuple[Path, str]:
    # Explicit verified build products; never rewrite installed PyInstaller.
    import pefile

    loader = pefile.PE(str(bootloader))
    imports = {entry.dll.decode().casefold() for entry in loader.DIRECTORY_ENTRY_IMPORT}
    if loader.OPTIONAL_HEADER.Subsystem != 2 or imports & {"user32.dll", "gdi32.dll", "comctl32.dll", "ole32.dll"}:
        raise ValueError("probe bootloader must have Windows subsystem and no GUI imports")
    if not loader.OPTIONAL_HEADER.DATA_DIRECTORY[5].Size:
        raise ValueError("probe bootloader requires relocations")
    output = output.absolute()
    output.mkdir(exist_ok=False, parents=True)
    crypto_path = stage_headless_crypto(crypto_library, output / "crypto-source")
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onedir",
        "--console",
        "--noupx",
        "--name",
        "dsh-feature-probe",
        "--distpath",
        str(output / "dist"),
        "--workpath",
        str(output / "work"),
        "--specpath",
        str(output / "spec"),
    ]
    for module in HEADLESS_EXCLUDED_MODULES:
        command.extend(("--exclude-module", module))
    command.extend(("--paths", str(native_extension.absolute().parent), "--paths", str(Path(__file__).resolve().parents[1]), "--paths", str(crypto_path)))
    command.extend(headless_crypto_arguments())
    command.extend(("--add-binary", str(crypto_path / "libsodium.dll") + ";."))
    command.append(str(Path(__file__).with_name("feature_probe_entry.py")))
    subprocess.run(command, check=True)
    bundle = output / "dist/dsh-feature-probe"
    exe = bundle / "dsh-feature-probe.exe"
    finalize_owned_headless_runtime(bundle, exe, bootloader)
    licenses = bundle / "LIBSODIUM-LICENSES"
    licenses.mkdir()
    for license_path in crypto_path.glob("LICENSE-*.txt"):
        (licenses / license_path.name).write_bytes(license_path.read_bytes())
    files = {p.relative_to(bundle).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(bundle.rglob("*")) if p.is_file()}
    raw = (json.dumps({"schema": 1, "entry": "dsh-feature-probe.exe", "files": files}, sort_keys=True, indent=2) + "\n").encode()
    (bundle / "bundle.json").write_bytes(raw)
    digest = hashlib.sha256(raw).hexdigest()
    print(json.dumps({"bundle": str(bundle), "manifest_digest": digest}))
    return bundle, digest


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    parser.add_argument("--bootloader", type=Path, required=True, help="owned onedir-only, GUI-free PyInstaller bootloader")
    parser.add_argument("--native-extension", type=Path, required=True, help="owned Qt-free native canary extension")
    parser.add_argument("--crypto-library", type=Path, required=True, help="pinned official libsodium 1.0.22 MSVC archive")
    args = parser.parse_args()
    build(args.output, bootloader=args.bootloader, native_extension=args.native_extension, crypto_library=args.crypto_library)
