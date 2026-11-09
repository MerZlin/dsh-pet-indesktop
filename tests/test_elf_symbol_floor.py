# -*- coding: utf-8 -*-
"""ELF 符号版本门禁（``scripts/check_elf_symbol_floor.py``）的行为与接线测试。

背景（issue #432）：Linux 发布包曾在 24.04 runner 上构建，PyInstaller 把构建机
的整套系统库（libX11/libgtk/libstdc++ 等 35 个）拷进 ``_internal/``，外加
setup-python 的 CPython 工件本身要求 GLIBC_2.38——Ubuntu 22.04（glibc 2.35）
用户一个 GLIBC 符号都解析不了。修复把构建基线钉回 22.04 容器，而这道门把
「产物 ELF 的符号版本地板 ≤ 22.04 系统所有」变成每次出包的硬校验：以后
runner/Python 工件/pip wheel 任一环节再漂移，出包当场红，而不是等用户报 issue。

这族守三件事：

1. **判据正确**：readelf 输出里 GLIBC/GLIBCXX 版本提取、地板比较（恰等地板
   通过、超一线失败）；
2. **门会红**：超地板文件必须失败，零 ELF 也必须失败（防路径手滑静默过）；
3. **接上了**：``build_linux.sh`` 每个变体出包后真的调用这道门。

单测用合成 readelf 文本与 ``/bin/true``（真 ELF、地板必然 ≤ 本机），不碰产物。
"""

from __future__ import annotations

import importlib.util
import shutil
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

# readelf 来自 binutils；Windows/macOS 测试腿没有它，真 ELF 集成用例只在
# linux + binutils 环境跑（纯函数用例不受限）。
HAS_READELF = shutil.which("readelf") is not None
LINUX_ONLY = pytest.mark.skipif(
    sys.platform != "linux" or not HAS_READELF,
    reason="真 ELF 集成用例需要 linux + readelf(binutils)",
)


@pytest.fixture(scope="module")
def gate():
    spec = importlib.util.spec_from_file_location(
        "check_elf_symbol_floor", REPO / "scripts" / "check_elf_symbol_floor.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["check_elf_symbol_floor"] = module
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------- 判据正确

def test_parse_version_handles_multi_component(gate):
    assert gate.parse_version("2.35") == (2, 35)
    assert gate.parse_version("2.4") == (2, 4)
    assert gate.parse_version("3.4.30") == (3, 4, 30)
    # 字典序会把 "2.4" 排在 "2.35" 后面——这正是事故扫描时踩过的坑，必须按元组比。
    assert gate.parse_version("2.4") < gate.parse_version("2.35")


def test_version_exceeds_boundaries(gate):
    """恰等地板通过；超一线失败；无版本要求（None）不算超。"""
    assert gate.version_exceeds((2, 36), (2, 35)) is True
    assert gate.version_exceeds((2, 38), (2, 35)) is True
    assert gate.version_exceeds((2, 35), (2, 35)) is False
    assert gate.version_exceeds((2, 17), (2, 35)) is False
    assert gate.version_exceeds((3, 4, 31), (3, 4, 30)) is True
    assert gate.version_exceeds((3, 4, 30), (3, 4, 30)) is False
    assert gate.version_exceeds(None, (2, 35)) is False


def test_extract_max_versions_from_readelf_text(gate):
    sample = "\n".join([
        "Symbol table '.dynsym' contains 5 entries:",
        "   Num:    Value          Size Type    Bind   Vis      Ndx Name",
        "     1: 0000000000000000     0 FUNC    GLOBAL DEFAULT  UND initgroups@GLIBC_2.2.5 (2)",
        "     2: 0000000000000000     0 FUNC    GLOBAL DEFAULT  UND strlen@GLIBC_2.2.5 (2)",
        "     3: 0000000000000000     0 FUNC    GLOBAL DEFAULT  UND __isoc23_strtol@GLIBC_2.38 (3)",
        "     4: 0000000000000000     0 FUNC    GLOBAL DEFAULT  UND _ZNSt...@GLIBCXX_3.4.30 (3)",
        "     5: 0000000000000000     0 FUNC    GLOBAL DEFAULT  UND _ZNSt8...@GLIBCXX_3.4 (2)",
    ])
    glibc, glibcxx = gate.extract_max_versions(sample)
    assert glibc == (2, 38)
    assert glibcxx == (3, 4, 30)


def test_extract_max_versions_empty_text(gate):
    assert gate.extract_max_versions("") == (None, None)
    assert gate.extract_max_versions("readelf: 错误：不是 ELF 文件") == (None, None)


# ---------------------------------------------------------------- 门会红（真 ELF）

@LINUX_ONLY
def test_system_elf_passes_default_floor(gate, tmp_path):
    """本机 /bin/true 的地板必然 ≤ 本机 glibc —— 默认地板下必须过门。"""
    shutil.copy("/bin/true", tmp_path / "true")

    report = gate.scan(tmp_path, gate.DEFAULT_GLIBC_FLOOR, gate.DEFAULT_GLIBCXX_FLOOR)

    assert report["elf_count"] == 1
    assert report["offenders"] == []
    assert report["max_glibc"] is not None
    assert report["max_glibc"] <= gate.DEFAULT_GLIBC_FLOOR


@LINUX_ONLY
def test_over_floor_elf_fails(gate, tmp_path):
    """把地板抬到 2.0，/bin/true（必引用 GLIBC_2.2.5 等）必须被点名为超线。"""
    shutil.copy("/bin/true", tmp_path / "true")

    report = gate.scan(tmp_path, (2, 0), gate.DEFAULT_GLIBCXX_FLOOR)

    assert report["elf_count"] == 1
    assert [entry[0].name for entry in report["offenders"]] == ["true"]


def test_non_elf_files_are_ignored(gate, tmp_path):
    (tmp_path / "readme.txt").write_text("not an elf", encoding="utf-8")
    (tmp_path / "fake.so").write_bytes(b"\x7fELFtruncated-but-not-a-real-elf")

    report = gate.scan(tmp_path, gate.DEFAULT_GLIBC_FLOOR, gate.DEFAULT_GLIBCXX_FLOOR)

    # 魔数过了但 readelf 解析不出动态符号的文件：算 ELF（魔数口径）但无版本要求。
    assert report["elf_count"] == 1
    assert report["offenders"] == []


def test_zero_elf_fails(gate, tmp_path):
    """一个 ELF 都扫不到 = --app-dir 八成给错了路径：必须失败而不是静默通过。"""
    (tmp_path / "empty.txt").write_text("", encoding="utf-8")

    report = gate.scan(tmp_path, gate.DEFAULT_GLIBC_FLOOR, gate.DEFAULT_GLIBCXX_FLOOR)

    assert report["elf_count"] == 0
    assert report["problems"]


def test_missing_dir_fails(gate, tmp_path):
    report = gate.scan(tmp_path / "nope", gate.DEFAULT_GLIBC_FLOOR, gate.DEFAULT_GLIBCXX_FLOOR)

    assert report["problems"]


@LINUX_ONLY
def test_main_exit_codes(gate, tmp_path, capsys):
    shutil.copy("/bin/true", tmp_path / "true")

    assert gate.main(["--app-dir", str(tmp_path)]) == 0
    assert "OK" in capsys.readouterr().out

    assert gate.main(["--app-dir", str(tmp_path), "--glibc-floor", "2.0"]) == 1
    assert "FAIL" in capsys.readouterr().err

    assert gate.main(["--app-dir", str(tmp_path / "nope")]) == 1


# ---------------------------------------------------------------- 构建接线
#
# 行为测试看不到"有没有接上"：门写得再对，没被构建脚本调用就什么也拦不住。
# 按源码文本断言 build_linux.sh 的接线与顺序（同 test_frameseq_bundle_gate /
# test_verify_rapidocr_models 的「构建接线」口径）。

BUILD_LINUX_SH = REPO / "scripts" / "build_linux.sh"


def test_build_linux_sh_invokes_floor_gate_per_variant():
    text = BUILD_LINUX_SH.read_text(encoding="utf-8")

    assert text.count("check_elf_symbol_floor.py") >= 1, (
        "build_linux.sh 必须调用 ELF 符号版本门禁，否则 22.04 基线只是口头承诺"
    )
    assert (text.index("check_elf_symbol_floor.py")
            < text.index('echo "Linux 构建完成')), "门必须在宣布构建完成前跑完所有变体"
