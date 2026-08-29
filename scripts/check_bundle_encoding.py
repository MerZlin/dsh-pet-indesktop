# -*- coding: utf-8 -*-
"""打包产物中文编码自检（issue #26：官方 Windows 包中文乱码）。

在 PyInstaller 构建完成后扫描产物目录，验证中文字面量在包内保持原样，
发现污染立即以非零退出码中止发布，防止再次发出乱码包：

1. Python 字节码（<name>.pyz / base_library.zip / 散落 .pyc）：
   反序列化 code 对象，收集全部字符串常量，断言已知中文字面量
   （吃Token/写代码/深色玻璃 等）原样存在。若构建环节把 UTF-8 源码
   按 GBK/cp1252 二次解码再编译，字面量会变成「合法但错误」的乱码，
   此检查必然失败；marshal 解析失败时退化为字节级扫描兜底。
2. 打包的文本资源（.json/.qss/.txt/.md）：严格按 UTF-8 解码（容忍 BOM），
   解码失败说明资源已被二次转码。
3. 中文素材文件名（如 吃Token.webm）在包内必须存在。

用法：
    python scripts/check_bundle_encoding.py --dir dist-onedir/dsh-pet-standalone-webm-chat

退出码：0 = 通过；1 = 发现乱码（构建脚本应中止并拒绝出包）。
无 Qt 依赖；优先使用 PyInstaller 自带的 reader 解析 onedir exe/.app 内嵌的
PYZ 字节码，未安装 PyInstaller 时退化为 zip/pyc/字节级扫描兜底。
"""
from __future__ import annotations

import argparse
import marshal
import sys
import zipfile
import zlib
from pathlib import Path
from typing import Iterable

# 这些字面量以原始中文形式存在于打包进所有变体的 .py 源码中
# （catalog/agent_link/proactive/speech_bubble），编译后必在字节码常量里。
EXPECTED_CODE_LITERALS = (
    "吃Token",
    "写代码",
    "原地敲击桌面互动",
    "深色玻璃",
    "让我看看……",
    "DSH 桥接插件安装失败",
)

# 必须原样存在的包内中文文件名（webm 变体；gif 变体同名换扩展名）
EXPECTED_FILENAME_NEEDLES = ("吃Token",)

# 必须能按 UTF-8 严格解码的文本资源扩展名（webm/音频等二进制不在此列）
TEXT_SUFFIXES = (".json", ".qss", ".txt", ".md")

_PYC_HEADER = 16  # CPython 3.7+ pyc 文件头长度（magic + flags + mtime + size）


def _iter_code_constants(code, acc: set[str]) -> None:
    """递归收集 code 对象及其嵌套 code 的全部字符串常量。"""
    for const in getattr(code, "co_consts", ()) or ():
        if isinstance(const, str):
            acc.add(const)
        elif hasattr(const, "co_consts"):
            _iter_code_constants(const, acc)


def scan_code_constants(data: bytes) -> set[str]:
    """尝试把字节流按 code 对象（pyc/marshal）反序列化并收集字符串常量。

    失败返回空集——调用方应退化到字节级扫描（见 ``_byte_scan_literals``）。
    """
    constants: set[str] = set()
    candidates = (data, data[_PYC_HEADER:])
    for candidate in candidates:
        try:
            obj = marshal.loads(candidate)
        except Exception:
            continue
        if hasattr(obj, "co_consts"):
            _iter_code_constants(obj, constants)
        elif isinstance(obj, tuple):  # 老格式 pyc 顶层是 (magic, code)
            for item in obj:
                if hasattr(item, "co_consts"):
                    _iter_code_constants(item, constants)
    return constants


def _byte_scan_literals(data: bytes, literals: Iterable[str]) -> set[str]:
    """字节级兜底：直接查找字面量的 UTF-8 字节序列。

    marshal 失败时使用。乱码编译产物中原始 UTF-8 字节必然缺失，
    因此命中即证明该字面量在字节码里原样存在。
    """
    found: set[str] = set()
    for literal in literals:
        if literal.encode("utf-8") in data:
            found.add(literal)
    return found


def _decompress_member(data: bytes) -> bytes:
    """PyInstaller PYZ 成员默认 zlib 压缩；存储模式直接返回原始字节。"""
    try:
        return zlib.decompress(data)
    except zlib.error:
        return data


def scan_zip_code_constants(path: Path, literals: Iterable[str]) -> set[str]:
    """扫描 zip（<name>.pyz / base_library.zip）内全部字节码常量。"""
    constants: set[str] = set()
    literal_list = list(literals)
    try:
        with zipfile.ZipFile(path) as archive:
            for info in archive.infolist():
                if not info.filename.endswith(".pyc"):
                    continue
                try:
                    raw = _decompress_member(archive.read(info))
                except Exception:
                    continue
                found = scan_code_constants(raw)
                if found:
                    constants |= found
                else:
                    constants |= _byte_scan_literals(raw, literal_list)
    except (OSError, zipfile.BadZipFile):
        pass
    return constants


def scan_pyc_file_constants(path: Path, literals: Iterable[str]) -> set[str]:
    """扫描散落的单个 .pyc 文件。"""
    try:
        raw = path.read_bytes()
    except OSError:
        return set()
    found = scan_code_constants(raw)
    if found:
        return found
    return _byte_scan_literals(raw, literals)


def _iter_pyinstaller_executables(root: Path):
    """返回 PyInstaller onedir 产物中可能嵌入 CArchive 的主程序路径。

    Windows 是根目录下的 .exe；macOS .app 是 Contents/MacOS 下的可执行文件；
    Linux 是根目录下无扩展名的可执行文件（通常与目录同名）。
    """
    if root.name.endswith(".app"):
        macos_dir = root / "Contents" / "MacOS"
        if macos_dir.is_dir():
            for path in macos_dir.iterdir():
                if path.is_file():
                    yield path
            return
    for path in root.iterdir():
        if not path.is_file():
            continue
        if path.suffix.lower() == ".exe" or path.suffix == "":
            yield path


def scan_pyinstaller_executables(root: Path, literals: Iterable[str]) -> set[str]:
    """扫描 onedir 可执行文件内嵌的 CArchive/PYZ 字节码常量。

    PyInstaller onedir 的工程模块字节码位于主程序内嵌的 PYZ（ZlibArchive）里，
    顶层入口脚本位于 CArchive 的 PYSOURCE 条目中——两者都不是磁盘上的 .pyz
    文件，必须用 PyInstaller reader 解析。未安装 PyInstaller 时返回空集。
    """
    try:
        from PyInstaller.archive.readers import CArchiveReader
        from PyInstaller.archive.readers import PKG_ITEM_PYSOURCE, PKG_ITEM_PYZ
    except Exception:
        return set()

    constants: set[str] = set()
    literal_list = list(literals)
    for exe in _iter_pyinstaller_executables(root):
        try:
            archive = CArchiveReader(str(exe))
        except Exception:
            continue
        for name, entry in archive.toc.items():
            typecode = entry[4]
            try:
                if typecode == PKG_ITEM_PYZ:
                    pyz = archive.open_embedded_archive(name)
                    for module_name in pyz.toc:
                        try:
                            obj = pyz.extract(module_name)
                        except Exception:
                            continue
                        if hasattr(obj, "co_consts"):
                            _iter_code_constants(obj, constants)
                elif typecode == PKG_ITEM_PYSOURCE:
                    data = archive.extract(name)
                    if hasattr(data, "co_consts"):
                        _iter_code_constants(data, constants)
                    else:
                        found = scan_code_constants(data)
                        if found:
                            constants |= found
                        else:
                            constants |= _byte_scan_literals(data, literal_list)
            except Exception:
                continue
    return constants


def collect_code_constants(root: Path, literals: Iterable[str]) -> set[str]:
    """收集产物目录内全部字节码字符串常量。

    优先扫描 onedir 主程序内嵌的 PYZ/PYSOURCE（真实发布形态），再补充扫描
    zip/pyz/pyc 等散落字节码，覆盖非 onedir 或 build 目录场景。
    """
    constants: set[str] = set()
    constants |= scan_pyinstaller_executables(root, literals)
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix == ".pyz" or path.name == "base_library.zip":
            constants |= scan_zip_code_constants(path, literals)
        elif path.suffix == ".pyc":
            constants |= scan_pyc_file_constants(path, literals)
    return constants


def verify_text_resources(root: Path, suffixes: tuple[str, ...]) -> list[str]:
    """严格 UTF-8 校验包内文本资源，返回解码失败的相对路径列表。"""
    bad: list[str] = []
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in suffixes:
            continue
        try:
            path.read_text(encoding="utf-8-sig")  # 容忍 BOM，其余严格
        except (OSError, UnicodeDecodeError):
            bad.append(str(path.relative_to(root)))
    return bad


def verify_chinese_filenames(root: Path, needles: tuple[str, ...]) -> list[str]:
    """校验包内存在包含指定中文串的文件名，返回缺失列表。"""
    missing: list[str] = []
    for needle in needles:
        if not any(needle in path.name for path in root.rglob("*")):
            missing.append(needle)
    return missing


def check_bundle(
    root: Path,
    code_literals: Iterable[str] = EXPECTED_CODE_LITERALS,
    filename_needles: tuple[str, ...] = EXPECTED_FILENAME_NEEDLES,
    text_suffixes: tuple[str, ...] = TEXT_SUFFIXES,
) -> list[str]:
    """对产物目录执行全部编码检查，返回错误信息列表（空 = 通过）。"""
    errors: list[str] = []

    constants = collect_code_constants(root, code_literals)
    # 字节码常量可能包含完整句子（如 "深色玻璃 · 右上方"），因此按子串匹配：
    # 只要某个常量里原样包含该中文片段，即认为该字面量未被编码污染。
    found_literals = {
        lit for lit in code_literals if any(lit in const for const in constants)
    }
    missing = [lit for lit in code_literals if lit not in found_literals]
    if missing:
        errors.append(
            "字节码中缺少中文常量（疑似源码被按非 UTF-8 编码二次解码编译）: "
            + ", ".join(missing)
        )

    bad_texts = verify_text_resources(root, text_suffixes)
    if bad_texts:
        errors.append(
            "以下文本资源无法按 UTF-8 解码（疑似被二次转码）: "
            + ", ".join(bad_texts)
        )

    missing_files = verify_chinese_filenames(root, filename_needles)
    if missing_files:
        errors.append("包内缺少中文名素材文件: " + ", ".join(missing_files))

    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="PyInstaller 产物中文编码自检（issue #26）"
    )
    parser.add_argument(
        "--dir",
        required=True,
        help="构建产物目录（如 dist-onedir/dsh-pet-standalone-webm-chat）",
    )
    args = parser.parse_args(argv)

    root = Path(args.dir)
    if not root.is_dir():
        print(f"[encoding-check] 产物目录不存在: {root}", file=sys.stderr)
        return 1

    print(f"[encoding-check] 扫描产物目录: {root}")
    errors = check_bundle(root)
    if not errors:
        print("[encoding-check] PASS: 中文编码检查全部通过（字面量/资源/文件名原样保持 UTF-8）")
        return 0
    print("[encoding-check] FAIL: 发现中文编码污染，拒绝出包：", file=sys.stderr)
    for error in errors:
        print(f"  - {error}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
