"""Build owned Windows-subsystem probe components without modifying PyInstaller.

The pinned upstream bootloader keeps onedir/PEP-587 loading. Console/window
control and onefile child creation are removed, not relaxed at run time. The
probe's LPAC Job already forbids children. No release trust anchor is generated.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tarfile
from pathlib import Path

PYINSTALLER_VERSION = "6.20.0"
SOURCE_SHA256 = "95c5c7e03d5d61e9dfb8ef259c699cf492bb1041beb6dbe83696608cec07347a"


def extract_source(archive: Path, output: Path) -> Path:
    with archive.open("rb") as handle:
        if hashlib.file_digest(handle, "sha256").hexdigest() != SOURCE_SHA256:
            raise ValueError("PyInstaller source digest mismatch")
    output.mkdir(parents=True, exist_ok=False)
    with tarfile.open(archive) as tar:
        for member in tar:
            prefix = "pyinstaller-" + PYINSTALLER_VERSION + "/"
            if not member.name.startswith(prefix):
                raise ValueError("source archive root")
            relative = Path(member.name[len(prefix) :])
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError("source archive path")
            if not relative.parts or not (relative.parts[0] == "bootloader" or str(relative) == "COPYING.txt"):
                continue
            if member.issym() or member.islnk():
                raise ValueError("source archive link")
            if not member.isfile():
                continue
            target = output / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            stream = tar.extractfile(member)
            if stream is None:
                raise ValueError("source archive file")
            with target.open("xb") as handle:
                handle.write(stream.read())
    return output / "bootloader"


def patch_bootloader(source: Path) -> None:
    path = source / "src/pyi_utils_win32.c"
    text = path.read_text(encoding="utf-8")
    start = text.index("static BOOL WINAPI\n_pyi_win32_console_ctrl")
    end = text.index("static wchar_t *\n_pyi_win32_get_sid", start)
    # Retain later SID and permission utilities. A onefile path fails closed.
    text = text[:start] + "int pyi_utils_create_child(struct PYI_CONTEXT *ctx) { return -1; }\n\n" + text[end:]
    start = text.index("static void pyi_win32_adjust_console")
    end = text.index("#endif /* !defined(WINDOWED) */", start)
    text = text[:start] + "void pyi_win32_hide_console(void) {}\nvoid pyi_win32_minimize_console(void) {}\n\n" + text[end:]
    path.write_text(text, encoding="utf-8")
    # Preserve numeric errors even when a localized system message is absent.
    path = source / "src/pyi_dylib_python.c"
    text = path.read_text(encoding="utf-8")
    needle = "if (dylib->handle == NULL) {\n        PYI_WINERROR_W"
    if text.count(needle) != 1:
        raise ValueError("bootloader load-error seam")
    text = text.replace(
        needle, 'if (dylib->handle == NULL) {\n        PYI_ERROR("probe Python DLL load error=%lu\\n", (unsigned long)GetLastError());\n        PYI_WINERROR_W'
    )
    path.write_text(text, encoding="utf-8")


def bootloader_command(compiler: str, source: Path, output: Path, *, debug: bool = False) -> list[str]:
    command = [
        compiler,
        "-Os",
        "-municode",
        "-mwindows",
        "-DWIN32",
        "-D_UNICODE",
        "-DUNICODE",
        "-DNDEBUG",
        "-D_WIN32_WINNT=0x0601",
        "-DNTDDI_VERSION=0x06010000",
        "-DHAVE_STRDUP",
        "-DHAVE_STRNDUP",
        "-DHAVE_STRNLEN",
        "-DHAVE_WCSDUP",
        "-DHAVE_STDBOOL_H",
    ]
    if debug:
        command.append("-DLAUNCH_DEBUG")
    command.extend("-I" + str(source / subdir) for subdir in ("src", "windows", "zlib"))
    command.extend(str(file) for subdir in ("src", "zlib") for file in sorted((source / subdir).glob("*.c")))
    command.extend(
        [
            "-static-libgcc",
            "-Wl,--dynamicbase,--nxcompat,--high-entropy-va,--gc-sections",
            "-Wl,--pic-executable,--image-base,0x140000000,--entry,WinMainCRTStartup",
            "-lkernel32",
            "-ladvapi32",
            "-o",
            str(output),
        ]
    )
    return command


def build(output: Path, *, archive: Path, compiler: str, debug: bool = False):
    output = output.absolute()
    if output.exists():
        raise FileExistsError(output)
    if os.name != "nt" or sys.version_info[:2] != (3, 11):
        raise RuntimeError("validated native build requires Windows CPython 3.11 x64")
    source = extract_source(archive, output / "source")
    patch_bootloader(source)
    loader = output / "probe-run.exe"
    extension = output / "_dsh_probe_native.pyd"
    commands = [
        bootloader_command(compiler, source, loader, debug=debug),
        [
            compiler,
            "-shared",
            "-Os",
            "-static-libgcc",
            "-I" + str(Path(sys.base_prefix) / "Include"),
            str(Path(__file__).with_name("feature_probe_native_extension.c").absolute()),
            str(Path(sys.base_prefix) / "libs/python311.lib"),
            "-ladvapi32",
            "-lcrypt32",
            "-lkernel32",
            "-o",
            str(extension),
        ],
    ]
    (output / "commands.json").write_text(json.dumps(commands, indent=2), encoding="utf-8")
    for index, command in enumerate(commands):
        result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=120)
        (output / f"compile-{index}.log").write_bytes(result.stdout)
        result.check_returncode()
    # Distribute the upstream copyright and exception alongside the helper.
    metadata = {
        "schema": 1,
        "pyinstaller": PYINSTALLER_VERSION,
        "source_sha256": SOURCE_SHA256,
        "compiler": compiler,
        "commands": commands,
        "files": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (loader, extension)},
    }
    (output / "native-build.json").write_text(json.dumps(metadata, sort_keys=True, indent=2), encoding="utf-8")
    return loader, extension


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    parser.add_argument("--source", type=Path, required=True, help="pinned PyInstaller 6.20.0 sdist")
    parser.add_argument("--compiler", default="gcc")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()
    products = build(args.output, archive=args.source, compiler=args.compiler, debug=args.debug)
    print(json.dumps({"bootloader": str(products[0]), "extension": str(products[1])}))
