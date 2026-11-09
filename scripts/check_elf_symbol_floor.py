#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ELF 符号版本门禁：产物里任何 ELF 引用的 GLIBC/GLIBCXX 版本不得超地板。

背景（issue #432，2026-11-10 定位）：Linux 包曾在 ubuntu-latest（GitHub 已整体
漂到 24.04/glibc 2.38）上构建，而 PyInstaller 在 Linux 上会把**构建机上解析到的
系统库整份拷进 ``_internal/``**，叠加 setup-python 的 CPython 工件本身也换到
24.04 基线重编——Ubuntu 22.04（glibc 2.35）用户启动即报
``version `GLIBC_2.38' not found``。修复把构建基线钉回 22.04 容器 + uv 管的
CPython；本门把地板变成每次出包的硬校验，两个元凶（runner 镜像、预编译 Python
工件）都是会自己动的移动靶——这道门存在的理由就是让它们再动时**当场红**，
而不是等下一个用户来报 issue。

地板取值 = Ubuntu 22.04（jammy）系统自带的上限：

- ``GLIBC 2.35``：jammy 的 glibc 版本；
- ``GLIBCXX 3.4.30``：jammy 的 libstdc++ 6.0.30 所提供的最高 ABI 版本。
  注意 PyInstaller 会把构建机（= jammy）的 libstdc++ 拷进包，所以对 GLIBCXX 的
  要求同时受「别的库需要的版本 ≤ 3.4.30」约束——若将来某个 wheel 需要更高
  GLIBCXX，本门会红出来，届时必须显式决策（提高基线声明或换 bundled libstdc++），
  不许静默绕过。

用法::

    python scripts/check_elf_symbol_floor.py --app-dir dist/dsh-pet-standalone-webm

实现约束：纯 stdlib；readelf 来自 binutils（jammy 容器里由 build-linux.yml 显式
安装）。零 ELF 视为失败——路径给错时静默通过比没有门更糟。
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

# Ubuntu 22.04（jammy）基线；调整这两个值 = 调整"支持的最低 Linux 发行版"声明，
# 必须同步 README 的系统要求与 docs/ONEDIR_PACKAGING.md §六。
DEFAULT_GLIBC_FLOOR: tuple[int, ...] = (2, 35)
DEFAULT_GLIBCXX_FLOOR: tuple[int, ...] = (3, 4, 30)

_ELF_MAGIC = b"\x7fELF"
_VERSION_RE = re.compile(r"(GLIBCXX|GLIBC)_([0-9]+(?:\.[0-9]+)*)")


def parse_version(text: str) -> tuple[int, ...]:
    """``"2.35"`` → ``(2, 35)``。元组比较才与版本序一致（字典序会把 2.4 排到 2.35 后）。"""
    return tuple(int(part) for part in text.split("."))


def version_exceeds(
    version: tuple[int, ...] | None, floor: tuple[int, ...]
) -> bool:
    """version 是否**严格超过**地板；无版本要求（None）不算超。"""
    if version is None:
        return False
    return version > floor


def extract_max_versions(readelf_output: str) -> tuple[tuple[int, ...] | None,
                                                       tuple[int, ...] | None]:
    """从 ``readelf --dyn-syms --wide`` 输出提取 GLIBC / GLIBCXX 引用的最高版本。"""
    max_glibc: tuple[int, ...] | None = None
    max_glibcxx: tuple[int, ...] | None = None
    for prefix, version_text in _VERSION_RE.findall(readelf_output):
        version = parse_version(version_text)
        if prefix == "GLIBC":
            if max_glibc is None or version > max_glibc:
                max_glibc = version
        elif max_glibcxx is None or version > max_glibcxx:
            max_glibcxx = version
    return max_glibc, max_glibcxx


def _read_elf_symbols(path: Path) -> str:
    result = subprocess.run(
        ["readelf", "--dyn-syms", "--wide", str(path)],
        capture_output=True, text=True, check=False,
    )
    return result.stdout + result.stderr


def scan(
    app_dir: Path,
    glibc_floor: tuple[int, ...] = DEFAULT_GLIBC_FLOOR,
    glibcxx_floor: tuple[int, ...] = DEFAULT_GLIBCXX_FLOOR,
) -> dict:
    """扫描目录下所有 ELF 的动态符号版本需求。

    返回 report：``elf_count``（魔数口径的 ELF 数）、``offenders``（超线
    ``(path, max_glibc, max_glibcxx)`` 列表）、全局 ``max_glibc`` / ``max_glibcxx``
    与 ``problems``（结构性问题，如目录不存在 / 零 ELF / readelf 缺失）。
    """
    problems: list[str] = []
    offenders: list[tuple[Path, tuple[int, ...] | None, tuple[int, ...] | None]] = []
    elf_count = 0
    global_glibc: tuple[int, ...] | None = None
    global_glibcxx: tuple[int, ...] | None = None

    if not app_dir.is_dir():
        return {"elf_count": 0, "offenders": [], "max_glibc": None,
                "max_glibcxx": None,
                "problems": [f"目录不存在：{app_dir}"]}
    if shutil.which("readelf") is None:
        problems.append("缺少 readelf（binutils）——无法扫描 ELF 符号版本")

    for path in sorted(app_dir.rglob("*")):
        if not path.is_file():
            continue
        try:
            head = path.open("rb").read(4)
        except OSError:
            continue
        if head != _ELF_MAGIC:
            continue
        elf_count += 1
        glibc, glibcxx = extract_max_versions(_read_elf_symbols(path))
        if version_exceeds(glibc, glibc_floor) or version_exceeds(glibcxx, glibcxx_floor):
            offenders.append((path, glibc, glibcxx))
        if global_glibc is None or (glibc is not None and glibc > global_glibc):
            global_glibc = glibc
        if global_glibcxx is None or (glibcxx is not None and glibcxx > global_glibcxx):
            global_glibcxx = glibcxx

    if elf_count == 0:
        problems.append(f"在 {app_dir} 下一个 ELF 都没扫到——路径是否给错？")

    return {"elf_count": elf_count, "offenders": offenders,
            "max_glibc": global_glibc, "max_glibcxx": global_glibcxx,
            "problems": problems}


def _format(version: tuple[int, ...] | None) -> str:
    return ".".join(str(part) for part in version) if version else "无"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="校验产物 ELF 的 GLIBC/GLIBCXX 符号版本地板（issue #432）")
    parser.add_argument("--app-dir", required=True, help="onedir 产物目录（如 dist/dsh-pet-standalone-webm）")
    parser.add_argument("--glibc-floor", default=".".join(map(str, DEFAULT_GLIBC_FLOOR)),
                        help="glibc 地板（默认 2.35 = Ubuntu 22.04）")
    parser.add_argument("--glibcxx-floor", default=".".join(map(str, DEFAULT_GLIBCXX_FLOOR)),
                        help="GLIBCXX 地板（默认 3.4.30 = jammy libstdc++ 上限）")
    args = parser.parse_args(argv)

    report = scan(Path(args.app_dir), parse_version(args.glibc_floor),
                  parse_version(args.glibcxx_floor))

    for problem in report["problems"]:
        print(f"FAIL {problem}", file=sys.stderr)
    for path, glibc, glibcxx in sorted(
            report["offenders"], key=lambda entry: entry[1] or (0,), reverse=True):
        print(f"FAIL GLIBC {_format(glibc)} / GLIBCXX {_format(glibcxx)}  {path}",
              file=sys.stderr)
    if report["problems"] or report["offenders"]:
        print(f"FAIL ELF 符号版本地板（GLIBC ≤ {args.glibc_floor}，"
              f"GLIBCXX ≤ {args.glibcxx_floor}）——产物无法在该基线的系统上运行",
              file=sys.stderr)
        return 1

    print(f"OK ELF 符号版本地板：扫描 {report['elf_count']} 个 ELF，"
          f"最高 GLIBC {_format(report['max_glibc'])} / GLIBCXX {_format(report['max_glibcxx'])}"
          f"（地板 {args.glibc_floor} / {args.glibcxx_floor}）")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
