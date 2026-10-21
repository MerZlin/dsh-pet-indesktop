# -*- coding: utf-8 -*-
"""随包分发的帧率采集后端（PresentMon）完整性硬门（构建管线，2026-10-11）。

**为什么需要这道门**：游戏帧率段的读数是桌面宠物**起一个子进程**跑官方独立
控制台程序拿到的（``pet/island/fps_monitor.py`` 的 ``FpsSession``），程序本体是随 Windows
包分发的二进制（``resources/presentmon/`` 下的 ``game_fps.BACKEND_EXE_NAME``）。
它缺失或换版时
产品**不会崩**：段按 ``unsupported`` 不占位、卡片写「缺少内置采集程序」——也就是
说，一个少打了后端的包在界面上看起来完全正常，只是这个功能永远不出数。缺件必须
在 PyInstaller 起跑**之前**挡住，且打完之后还要在产物里再验一次（拷贝截断同样致命）。

**判据**（与运行时同源，不是这里的第二份字面量）：
文件名 = ``pet/island/game_fps.py::BACKEND_EXE_NAME``；SHA256 = ``REQUIRED_SHA256``；
另外校验 PE 头里的机器类型必须是 x86-64 —— 上游 release 资产里 x64 与 ARM64 是
两个文件，名字不同但**产品只支持 x64**，架构校验让「混进 ARM 二进制」在构建期
就红，而不是等用户在 ARM 机器上看到一段永远采不到数的读数。
运行期**不**做摘要校验（每次启动读 980KB 只为一个 CI 该做的事，见 ``game_fps`` 注释）。

**约束**：只读文件系统（一个字节都不写）；只 import ``pet.island.game_fps``（零 Qt、零子
进程、零系统调用，连带链是 ``pet/__init__.py`` + ``pet/island_segments``，都是标准库），
因此不拉 PySide6、可在最小构建机环境跑。

用法：
    python scripts/verify_presentmon.py                      # 验仓库里的资源
    python scripts/verify_presentmon.py --path <exe>         # 验指定文件（验产物用）
    python scripts/verify_presentmon.py --json               # 机器可读输出

退出码：0 = 存在、文件名对、sha256 对、架构是 x86-64；1 = 缺失 / 名称不符 / 摘要
不符 / 非 x64（明细打到 stderr，含官方来源与期望值）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import struct
import sys
from pathlib import Path

# PE 头的机器类型（IMAGE_FILE_MACHINE_*）。只认 x86-64：产品支持的就是它。
PE_MACHINE_X64 = 0x8664
PE_MACHINE_NAMES = {
    0x014C: "x86",
    0x8664: "x86-64",
    0xAA64: "ARM64",
    0x01C4: "ARMv7",
}

#: 官方来源与期望值文案（失败时打印，省得去看代码）。
SOURCE_HINT = (
    "官方来源：https://github.com/GameTechDev/PresentMon/releases/tag/v2.6.0"
    " → 资产 PresentMon-2.6.0-x64.exe（MIT，随包附 resources/presentmon/LICENSE.txt）"
)


def repo_root() -> Path:
    """仓库根（本脚本在 ``<root>/scripts/`` 下）。"""
    return Path(__file__).resolve().parents[1]


def load_expectations():
    """从 ``pet/game_fps.py`` 取期望值（名字 + 摘要 + 相对目录）——**单一真相源**。

    这里刻意 import 产品模块而不是抄一份常量：抄一份的下场是"改了产品没改门"或
    反过来，而这两件事都会让门失去意义。
    """
    root = repo_root()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from pet.island import game_fps  # noqa: PLC0415 —— 路径先就位才能 import

    expected = root.joinpath(*game_fps.BACKEND_RELATIVE_DIR, game_fps.BACKEND_EXE_NAME)
    return expected, game_fps.BACKEND_EXE_NAME, game_fps.REQUIRED_SHA256


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def pe_machine(path: Path) -> int | None:
    """读 PE 头的 ``IMAGE_FILE_HEADER.Machine``；不是合法 PE 返回 ``None``。"""
    try:
        with path.open("rb") as handle:
            header = handle.read(0x40)
            if len(header) < 0x40 or header[:2] != b"MZ":
                return None
            offset = struct.unpack_from("<I", header, 0x3C)[0]
            handle.seek(offset)
            signature = handle.read(6)
    except OSError:
        return None
    if len(signature) < 6 or signature[:4] != b"PE\x00\x00":
        return None
    return struct.unpack_from("<H", signature, 4)[0]


def inspect(path: Path, expected_name: str, expected_sha: str) -> dict:
    """给一个候选文件出体检报告（不抛异常，结论都在返回值里）。"""
    report: dict = {
        "path": str(path),
        "expected_name": expected_name,
        "expected_sha256": expected_sha,
        "exists": path.exists(),
        "is_file": path.is_file(),
        "name": path.name,
        "name_ok": path.name == expected_name,
        "size": None,
        "sha256": None,
        "sha256_ok": False,
        "machine": None,
        "machine_name": None,
        "machine_ok": False,
        "problems": [],
    }
    if not report["is_file"]:
        report["problems"].append(
            "文件不存在" if not report["exists"] else "不是一个普通文件（目录？）")
        return report

    report["size"] = path.stat().st_size
    if not report["name_ok"]:
        report["problems"].append(
            f"文件名不符：期望 {expected_name}，实际 {path.name}"
            "（换版本必须同时改文件名/SHA256/SOURCE_TEXT，同名不同版是静默替换）")
    try:
        report["sha256"] = file_sha256(path)
    except OSError as error:  # 读不动就别假装读过
        report["problems"].append(f"读文件失败：{error}")
        return report
    report["sha256_ok"] = report["sha256"] == expected_sha
    if not report["sha256_ok"]:
        report["problems"].append(
            f"sha256 不符：期望 {expected_sha}，实际 {report['sha256']}")

    report["machine"] = pe_machine(path)
    report["machine_name"] = PE_MACHINE_NAMES.get(report["machine"])
    report["machine_ok"] = report["machine"] == PE_MACHINE_X64
    if not report["machine_ok"]:
        got = report["machine_name"] or (
            "非 PE 文件" if report["machine"] is None else f"0x{report['machine']:04X}")
        report["problems"].append(f"架构不是 x86-64：实际 {got}（产品只支持 x64）")
    return report


def format_human(report: dict) -> str:
    lines = [f"检查：{report['path']}"]
    if not report["is_file"]:
        lines.append("  文件缺失或不是普通文件")
    else:
        lines.append(f"  文件名：{report['name']}（期望 {report['expected_name']}）"
                     f"{'[OK]' if report['name_ok'] else '[不符]'}")
        lines.append(f"  大小：{report['size']} 字节")
        lines.append(f"  sha256：{report['sha256']}"
                     f"{'[OK]' if report['sha256_ok'] else '[不符]'}")
        lines.append(f"  架构：{report['machine_name'] or '未知'}"
                     f"{'[OK]' if report['machine_ok'] else '[不符]'}")
    for problem in report["problems"]:
        lines.append(f"  问题：{problem}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    expected_path, expected_name, expected_sha = load_expectations()
    parser = argparse.ArgumentParser(
        description="校验随包分发的 PresentMon 后端（名称 / sha256 / x64 架构）")
    parser.add_argument(
        "--path", default=None,
        help="被检查的文件；省略即检查仓库资源 "
             f"({expected_path.relative_to(repo_root()).as_posix()})")
    parser.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    args = parser.parse_args(argv)

    target = Path(args.path).expanduser() if args.path else expected_path
    report = inspect(target, expected_name, expected_sha)
    ok = not report["problems"]

    if args.json:
        print(json.dumps({**report, "ok": ok}, ensure_ascii=False, indent=2))
    else:
        (sys.stdout if ok else sys.stderr).write(format_human(report) + "\n")
        if not ok:
            sys.stderr.write(
                "FAIL: 随包分发的帧率采集后端不完整——构建中止（缺件的包在界面上"
                "看不出来，只会让游戏帧率段永远不出数）。\n" + SOURCE_HINT + "\n"
                f"期望文件：{expected_path}\n")
        else:
            print("OK: 帧率采集后端完整（名称 / sha256 / x86-64 三项一致）")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
