"""安装向导的扩展勾选必须是「真 opt-in」。

用户 2026-10-27 反馈：全新安装、什么都没勾，却在「扩展管理 → 加装扩展」里看到
Festival / Todo / Voice chime / Media keys。查证结论是**不是安装器装的**：

- 那 4 个目录在 ``%APPDATA%\\dsh-pet-core\\mods`` 里，时间戳 2026-10-21，
  ``voice.chime`` 版本 1.0.0（随包 examples 已 1.1.1）；当天日志有
  ``duplicate mod id 'voice.chime': …\\AppData\\… overridden by E:\\dsh-pet-core\\mods\\voice.chime``，
  说明是旧安装（安装目录）与用户目录两份并存后剩下的副本。

但这条约定太容易被后续改动破坏（少写一个 ``Tasks:`` 就会把扩展塞进每一个安装），
所以在这里把它钉住：
1. 凡 ``DestDir`` 落在 ``{app}\\mods\\`` 的 Source 必须带 ``Tasks: mod_*``；
2. 每个 ``mod_*`` Task 必须 ``Flags: unchecked``；
3. 构建脚本不得把 ``examples/petcore-mods`` 打进 onedir（否则「加装」变「随包」）；
4. 卸载只删 ``{app}\\mods``，不碰用户数据目录（用户自己导入的扩展不归安装器管）。
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ISS = ROOT / "packaging" / "petcore.iss"
BUILD = ROOT / "scripts" / "build_petcore.ps1"

_SOURCE_RE = re.compile(r"^Source:\s*(?P<src>\"[^\"]+\")\s*;\s*(?P<rest>.+)$")


def _source_lines() -> list[str]:
    text = ISS.read_text(encoding="utf-8")
    in_files = False
    lines: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("["):
            in_files = line == "[Files]"
            continue
        if in_files and line.startswith("Source:"):
            lines.append(line)
    return lines


def _tasks_of(line: str) -> str:
    match = re.search(r"Tasks:\s*([^;\]]+)", line)
    return match.group(1).strip() if match else ""


def _is_existing_only_refresh(line: str) -> bool:
    """已装扩展的升级同步行：DirExists + 未勾选 Task，只在既有目录上刷新。"""
    return (
        "Check:" in line
        and "DirExists(ExpandConstant('{app}\\mods\\" in line
        and "not IsTaskSelected('mod_" in line
    )


def test_every_bundled_mod_needs_an_explicit_task() -> None:
    mod_sources = [line for line in _source_lines() if r"{app}\mods" in line]
    assert mod_sources, "安装包应当还带着随附扩展（勾选才装）"

    for line in mod_sources:
        if _is_existing_only_refresh(line):
            continue
        tasks = _tasks_of(line)
        assert tasks.startswith("mod_"), f"这条扩展没绑 Task，会绕过勾选：{line}"


def test_every_mod_task_is_unchecked_by_default() -> None:
    text = ISS.read_text(encoding="utf-8")
    in_tasks = False
    mod_tasks: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("["):
            in_tasks = line == "[Tasks]"
            continue
        if in_tasks and line.startswith('Name: "mod_'):
            mod_tasks.append(line)
    assert mod_tasks, "[Tasks] 里应当有 mod_* 条目"

    for line in mod_tasks:
        assert "Flags: unchecked" in line, f"扩展勾选框默认被勾上了：{line}"


def test_installer_payload_never_ships_the_examples_mods() -> None:
    """onedir 里出现 ``mods/`` 就等于「没勾也装」——构建脚本不许把它打进去。"""
    text = BUILD.read_text(encoding="utf-8")

    for line in text.splitlines():
        if "--add-data" not in line:
            continue
        assert "examples" not in line.replace("\\", "/"), (
            f"构建脚本把 examples 打进了包：{line.strip()}"
        )
    # 随包只带内置 feature（core 自带），不带来 examples 的加装扩展
    assert "petcore/builtin" in text


def test_uninstall_only_removes_the_install_dir_mods() -> None:
    text = ISS.read_text(encoding="utf-8")
    deletes = [
        line.strip()
        for line in text.splitlines()
        if line.strip().startswith("Type: filesandordirs")
    ]
    assert deletes, "[UninstallDelete] 应当仍然清理安装目录里的 mods"

    for line in deletes:
        assert "{app}" in line, f"卸载清理越界到安装目录之外：{line}"
        assert "appdata" not in line.lower(), f"不许删用户数据目录：{line}"


def test_documented_regeneration_command_is_the_same_entry_point() -> None:
    """示例 mod 的源目录仍然只有一处，避免有人加第二份随包副本。"""
    manifests = list((ROOT / "examples" / "petcore-mods").glob("*/mod.json"))
    assert len(manifests) >= 5

    iss = ISS.read_text(encoding="utf-8").replace("\\", "/")
    for manifest in manifests:
        mod_id = manifest.parent.name
        assert f"examples/petcore-mods/{mod_id}" in iss, f"{mod_id} 没有安装入口，也没声明不安装"
