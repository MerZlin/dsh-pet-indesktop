# -*- coding: utf-8 -*-
"""运行时依赖必须显式声明在 requirements.txt 里。

背景（PR #134 的教训）：`pet/now_playing.py` 新引入 `import psutil` 做「播放器进程在不在」
判断，用来**绕开 SMTC 的永久阻塞**，但 psutil 没写进 requirements.txt。缺失时那段判断
按设计放行（宁可多试一次也不误伤），于是 SMTC 挂住的老路照旧会走——修复在打包版里
等于没生效，而 CI 里"碰巧装了 psutil"（PyInstaller/环境带进来的传递依赖），所以整套
测试全绿、谁也看不出问题。

这道守卫把「运行时 import 了却没人声明」变成红灯：扫 `pet/` 下所有第三方顶层 import，
逐个解析它属于哪个发行包，再核对 requirements.txt。

判定规则：
* 标准库、本仓库内的包（pet / packaging / integrations）跳过；
* 当前环境导不进来的模块跳过（平台条件依赖：非目标平台上本来就装不了，
  它们由目标平台的 CI 覆盖，不能在这里要求"能导入"）；
* 显式列在白名单里的传递依赖跳过（写着它由谁带进来，换依赖时会来改这里）。
"""
from __future__ import annotations

import ast
import importlib.metadata as md
import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
PET_DIR = REPO_ROOT / "pet"
REQUIREMENTS = REPO_ROOT / "requirements.txt"

# 本仓库内的顶层包名（不参与依赖核对）。
INTERNAL_MODULES = {"pet", "packaging", "integrations"}

# 由已声明依赖带进来的传递依赖：不是我们直接依赖的东西，但代码确实 import 了。
# 值 = 说明它由谁带进来（换依赖时回来改这里）。
TRANSITIVE_ALLOWED = {
    "comtypes": "pycaw 的依赖（pet/music_detect.py 走 pycaw 时用到）",
    "shiboken6": "PySide6 的依赖（pet/app.py 用它做对象有效性判断）",
    # 打包期为变体生成、运行期由 --add-data 带上的模块，不属于 pip 依赖。
    "build_variant": "构建期生成的变体标记（packaging/build_variant.py）",
}


def _declared_requirement_names() -> set[str]:
    """requirements.txt 里的发行包名（规范化：小写、「_/.」统一成「-」）。"""
    names = set()
    for raw in REQUIREMENTS.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        name = re.split(r"[<>=!;\[\s]", line, maxsplit=1)[0]
        if name:
            names.add(_canonical(name))
    return names


def _canonical(name: str) -> str:
    return name.lower().replace("_", "-").replace(".", "-")


def _pet_third_party_imports() -> dict[str, set[str]]:
    """pet/ 下所有顶层 import 名 → 引用它的文件（相对仓库根，便于读报错）。"""
    found: dict[str, set[str]] = {}
    for path in sorted(PET_DIR.rglob("*.py")):
        # 有些文件带 BOM，用 utf-8-sig 才解析得了。
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        rel = path.relative_to(REPO_ROOT).as_posix()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    found.setdefault(alias.name.split(".")[0], set()).add(rel)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                found.setdefault(node.module.split(".")[0], set()).add(rel)
    stdlib = set(sys.stdlib_module_names)
    return {
        mod: users
        for mod, users in found.items()
        if mod not in stdlib and mod not in INTERNAL_MODULES
    }


def _distributions_for(module: str) -> list[str]:
    """模块名 → 提供它的发行包名列表（可能多个，例如 winrt 命名空间包）。"""
    direct = md.packages_distributions().get(module)
    if direct:
        return list(direct)
    hits: list[str] = []
    for dist in md.distributions():
        name = dist.metadata["Name"]
        if not name:
            continue
        try:
            top_level = (dist.read_text("top_level.txt") or "").split()
        except Exception:  # noqa: BLE001 - 个别发行包读不了元数据
            continue
        if module in top_level:
            hits.append(name)
    return hits


def test_pet_third_party_imports_are_declared():
    """pet/ 里 import 的第三方包，必须能在 requirements.txt 里找到（或列入白名单）。"""
    declared = _declared_requirement_names()
    undeclared: list[str] = []
    for module, users in sorted(_pet_third_party_imports().items()):
        if module in TRANSITIVE_ALLOWED:
            continue
        try:  # 平台条件依赖在别的平台上装不上：跳过，由目标平台 CI 覆盖
            __import__(module)
        except Exception:  # noqa: BLE001 - 导入失败即视为当前平台不可用
            continue
        dists = _distributions_for(module)
        if not dists:
            continue  # 解析不出归属（例如仓库内相对路径注入的模块）
        if not any(_canonical(dist) in declared for dist in dists):
            undeclared.append(
                f"{module} (发行包: {', '.join(sorted(dists))}) <- {', '.join(sorted(users))}"
            )
    assert not undeclared, (
        "以下第三方模块被 pet/ import，但没有声明在 requirements.txt 里。\n"
        "打包版不会带上它们（用户也无法自行 pip 安装），而 CI 环境里往往碰巧存在，\n"
        "所以这种缺失在测试里看不出来。请加进 requirements.txt，"
        "并在需要时给构建脚本加 --collect-all：\n  " + "\n  ".join(undeclared)
    )


def test_psutil_is_declared_for_player_probe():
    """psutil 专项保险：歌词显示靠它绕开 SMTC 的永久阻塞，缺了就白修（见模块 docstring）。"""
    assert "psutil" in _declared_requirement_names(), (
        "psutil 必须留在 requirements.txt：pet/now_playing.py 用它判断播放器进程在不在，"
        "进程不在时直接启动播放器而**不碰 WinRT**；缺失时该判断放行，"
        "SMTC 卡住的老路会照旧走。"
    )
    from pet import now_playing

    assert callable(now_playing.player_process_running)
