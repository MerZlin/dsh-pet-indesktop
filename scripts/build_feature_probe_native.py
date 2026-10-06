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


def patch_long_path_loading(path_source: str, config_source: str) -> tuple[str, str]:
    """Use Win32 extended local paths, without changing discovery or permissions.

    MSVCRT _wfopen does not honor the executable's longPathAware manifest.
    CPython 3.11.1's getpath joins also need the explicit extended spelling:
    PathCchCombineEx can otherwise require four more characters than the buffer
    it receives. Keep the same owned onedir home and explicit search paths.
    """
    fopen = "    return _wfopen(wfilename, wmode);"
    home = "return _pyi_pyconfig_set_string(config, &config_impl->home, pyi_ctx->application_home_dir, dylib_python);"
    if path_source.count(fopen) != 1 or config_source.count(home) != 1:
        raise ValueError("bootloader long-path loading seam")
    path_source = path_source.replace(
        fopen,
        r"""    /* MSVCRT needs an explicit extended spelling of a local absolute path. */
    wchar_t extended_filename[PYI_PATH_MAX + 5];
    if (((wfilename[0] >= L'A' && wfilename[0] <= L'Z') ||
         (wfilename[0] >= L'a' && wfilename[0] <= L'z')) &&
        wfilename[1] == L':' && wfilename[2] == L'\\') {
        if (swprintf(extended_filename, PYI_PATH_MAX + 5, L"\\\\?\\%ls", wfilename) < 0) {
            return NULL;
        }
        return _wfopen(extended_filename, wmode);
    }
    return _wfopen(wfilename, wmode);""",
    )
    # Only this function's home initializer is changed. No registry, environment,
    # source-tree or installed-Python fallback is introduced.
    marker = "    /* Macro to avoid manual code repetition. */"
    function_start = config_source.find("pyi_pyconfig_pep587_set_python_home(")
    if function_start < 0:
        # Tiny source contracts contain only the target function body.
        function_start = 0
    position = config_source.find(marker, function_start)
    if position < 0:
        raise ValueError("bootloader long-path loading seam")
    prepare = r"""    const char *home_path = pyi_ctx->application_home_dir;
#ifdef _WIN32
    char extended_home[PYI_PATH_MAX + 5];
    if (((pyi_ctx->application_home_dir[0] >= 'A' && pyi_ctx->application_home_dir[0] <= 'Z') ||
         (pyi_ctx->application_home_dir[0] >= 'a' && pyi_ctx->application_home_dir[0] <= 'z')) &&
        pyi_ctx->application_home_dir[1] == ':' && pyi_ctx->application_home_dir[2] == '\\') {
        int written = snprintf(extended_home, sizeof(extended_home), "\\\\?\\%s", pyi_ctx->application_home_dir);
        if (written < 0 || (size_t)written >= sizeof(extended_home)) {
            return -1;
        }
        home_path = extended_home;
    }
#endif

"""
    config_source = config_source[:position] + prepare + config_source[position:]
    config_source = config_source.replace(home, "return _pyi_pyconfig_set_string(config, &config_impl->home, home_path, dylib_python);")
    return path_source, config_source


def patch_explicit_runtime_paths(main_source: str, config_source: str) -> tuple[str, str]:
    """Keep every CPython/PYZ pathname inside the same sealed local onedir.

    Python 3.11.1's getpath uses PathCch with an exactly sized join buffer.
    LPAC cannot read the global long-path policy; the Win32 routine can add an
    extended prefix and exceed that buffer, including a narrow 260-char edge.
    Use Python's documented explicit-path API with the SAME three paths already
    computed by the pinned loader. PEP-587 still configures isolation/argv.
    No registry, environment, source or installed-Python discovery is added.
    """
    executable = "    return _pyi_resolve_executable_win32(pyi_ctx->executable_filename);"
    paths = "    /* Set */\n    ret = _pyi_pyconfig_set_module_search_paths("
    if main_source.count(executable) != 1 or config_source.count(paths) != 1:
        raise ValueError("bootloader explicit runtime path seam")
    main_source = main_source.replace(
        executable,
        r"""    int resolved = _pyi_resolve_executable_win32(pyi_ctx->executable_filename);
    if (resolved < 0) return resolved;
    /* Propagate the local extended spelling into Python's embedded PYZ path. */
    if (((pyi_ctx->executable_filename[0] >= 'A' && pyi_ctx->executable_filename[0] <= 'Z') ||
         (pyi_ctx->executable_filename[0] >= 'a' && pyi_ctx->executable_filename[0] <= 'z')) &&
        pyi_ctx->executable_filename[1] == ':' && pyi_ctx->executable_filename[2] == '\\') {
        char extended_executable[PYI_PATH_MAX];
        int written = snprintf(extended_executable, sizeof(extended_executable), "\\\\?\\%s", pyi_ctx->executable_filename);
        if (written < 0 || (size_t)written >= sizeof(extended_executable)) return -1;
        memcpy(pyi_ctx->executable_filename, extended_executable, (size_t)written + 1);
    }
    return resolved;""",
    )
    prepare = r"""#ifdef _WIN32
    /* Explicit path initialization is validated only for this pinned runtime. */
    if (dylib_python->version != 311) { ret = -1; goto end; }
    wchar_t owned_search[3 * PYI_PATH_MAX + 3];
    size_t search_length = 0;
    owned_search[0] = L'\0';
    for (i = 0; i < 3; i++) {
        size_t length = wcslen(module_search_paths_w[i]);
        if (search_length + length + (i != 0) + 1 > sizeof(owned_search) / sizeof(wchar_t)) {
            ret = -1; goto end;
        }
        if (i) owned_search[search_length++] = L';';
        memcpy(owned_search + search_length, module_search_paths_w[i], (length + 1) * sizeof(wchar_t));
        search_length += length;
    }
    void (__cdecl *owned_set_path)(const wchar_t *) = (void (__cdecl *)(const wchar_t *))GetProcAddress(dylib_python->handle, "Py_SetPath");
    if (!owned_set_path) { ret = -1; goto end; }
    owned_set_path(owned_search);
#endif

"""
    return main_source, config_source.replace(paths, prepare + paths)


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
    path_source = source / "src/pyi_path.c"
    config_source = source / "src/pyi_pyconfig_pep587.c"
    path_text, config_text = patch_long_path_loading(path_source.read_text(encoding="utf-8"), config_source.read_text(encoding="utf-8"))
    path_source.write_text(path_text, encoding="utf-8")
    main_source = source / "src/pyi_main.c"
    main_text, config_text = patch_explicit_runtime_paths(main_source.read_text(encoding="utf-8"), config_text)
    main_source.write_text(main_text, encoding="utf-8")
    config_source.write_text(config_text, encoding="utf-8")


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
