# -*- coding: utf-8 -*-
"""打包产物语音合成（edge-tts）自检：TTS 栈必须真的进了包。

**为什么需要它**（2026-09-22 实测）：`PyInstaller --collect-all edge_tts` 在
打包机**没装** edge-tts 时只打印一行 WARNING
（``collect_data_files - skipping data collection for module 'edge_tts' as it is
not a package``）就继续构建，产物静默缺 TTS；用户拿到包后点「立即报时」只看到
「请运行 pip install edge-tts」——而打包版用户根本没地方 pip install。构建期
判红是唯一能拦住它的位置。

**怎么判**（不依赖网络、不启动 GUI）：

1. 在**打包机环境**里算出 edge-tts 的导入闭包，这就是应用运行时真正要用的模块集；
   打包机装不上 edge-tts 就直接判失败——那正是上面那个静默失败的前提。
2. 从**产物 exe** 里读真实清单：CArchive → 内嵌 `PYZ.pyz` → 模块名集合，
   再并上 `_internal` 目录里落盘的 `.py/.pyd/.so`（`--collect-all` 会把包源码
   作为数据文件复制一份，两条路都要认）。
3. 闭包里缺任何一个 → 中止构建。

注意两个**踩过的坑**，都会让判定假红（构建期中止一个本来正常的产物）：

* 只看 `_internal/` 目录会假阴性。纯 Python 依赖（实测 `tabulate`）只进 exe 内嵌
  的 PYZ，磁盘上根本没有对应目录——`edge_tts/util.py` 是
  `from tabulate import tabulate`，漏了它运行时就是 ImportError。
* 闭包不能按「`import edge_tts` 之后 `sys.modules` 里所有非标准库模块」算：那样会
  把启动期注入的模块（`.pth` / `sitecustomize`）与运行时合成模块一并算成"必须有"。
  实际踩到的假红见 `_has_packable_source` 与 `import_closure` 的说明。

用法：
    python scripts/verify_bundle_tts.py --app-dir dist-onedir/dsh-pet-standalone-webm-chat
    python scripts/verify_bundle_tts.py --exe <path to exe>

退出码：0 通过；2 目录/exe 缺失；3 清单读不出来；4 缺模块；5 打包机没装 edge-tts。
stdout 末行打印 JSON 摘要（ASCII），供构建脚本记录。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# 底部兜底清单：即使闭包测量退化（例如打包机装了 edge-tts 但 import 抛错被吞），
# 这些模块也必须在包里。`tabulate` 在列——它是 PYZ-only 的那类依赖。
MINIMUM_MODULES = (
    "edge_tts",
    "edge_tts.communicate",
    "edge_tts.util",
    "tabulate",
    "aiohttp",
    "aiohttp.client",
    "certifi",
)


def _has_packable_source(module) -> bool:
    """这个模块有值得打进包的源码吗？

    两个「不算」的情形（2026-09-22 在装了 matplotlib/PyQt5 的普通 CPython 3.11
    上实测踩到，两条都曾让真产物被判红）：

    * **运行时合成模块**：``cython_runtime`` 没有 ``__file__``——它由 Cython 生成的
      扩展在 import 时现造，任何产物都不可能也不该为它准备文件；``__file__`` 指向
      已消失路径的残留条目同理。
    * **pkgutil 风格 namespace 空壳**：``backports`` 的 ``__init__.py`` 只有一行
      ``extend_path``，自身没有代码。aiohttp 的 ``from backports.zstd import ...``
      会先把这个空壳建出来再失败（``backports.zstd`` 只是 aiohttp 的 speedups
      extra，未装且包在 ``try/except ImportError`` 里），于是它出现在
      ``sys.modules`` 里——要求一个空壳进包是假要求。
    """
    path = getattr(module, "__file__", None)
    if not path:
        return False
    source_file = Path(path)
    if not source_file.is_file():
        return False
    if source_file.name == "__init__.py":
        try:
            source = source_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return True
        if "extend_path(" in source:
            return False
    return True


def import_closure(module_name: str = "edge_tts") -> set[str] | None:
    """打包机环境里 ``module_name`` 的导入闭包（去掉标准库与内建）。

    三条过滤，缺一条就会把无关模块算成"必须有"（都是实测踩出来的）：

    * **按导入前后做差**：``.pth`` / ``sitecustomize`` 在解释器启动时就塞进
      ``sys.modules`` 的模块与被检查的库无关——实测这台打包机启动时已有 ``google``
      / ``mpl_toolkits`` / ``pywin32_bootstrap``（来自 Dev-Cpp 自带的
      site-packages）。只认"这次 ``import`` 新拉进来的"。
    * **只认有源码的模块**（``_has_packable_source``）。
    * 标准库与内建模块不算。

    返回 ``None`` 表示这个模块在打包机上不可导入——对构建脚本来说这是**失败**，
    不是"跳过"。
    """
    stdlib = set(getattr(sys, "stdlib_module_names", ()))
    before = set(sys.modules)
    try:
        __import__(module_name)
    except Exception:  # noqa: BLE001 - 任何导入失败都等同"没装"
        return None
    closure: set[str] = set()
    for name, module in list(sys.modules.items()):
        if name in before or module is None:
            continue
        top = name.split(".")[0]
        if not top or top.startswith("_"):
            continue
        if top in stdlib or top in sys.builtin_module_names:
            continue
        if not _has_packable_source(module):
            continue
        closure.add(name)
    return closure


def _disk_module_names(internal_dir: Path) -> set[str]:
    """`_internal` 里落盘的 Python 模块名（.py/.pyd/.so，含包内 __init__）。"""
    names: set[str] = set()
    if not internal_dir.is_dir():
        return names
    for path in internal_dir.rglob("*"):
        if not path.is_file():
            continue
        suffix = path.suffix.lower()
        if suffix not in (".py", ".pyd", ".so"):
            continue
        rel = path.relative_to(internal_dir)
        parts = list(rel.parts)
        stem = parts[-1]
        if stem == "__init__.py":
            parts = parts[:-1]
        elif suffix == ".py":
            parts[-1] = stem[: -len(".py")]
        else:
            # 扩展模块可能是 pkg/_x.cp311-win_amd64.pyd，取第一个点之前的部分。
            parts[-1] = stem.split(".")[0]
        if parts:
            names.add(".".join(parts))
    return names


def frozen_module_names(exe: Path) -> set[str]:
    """产物 exe 里的模块名集合（内嵌 PYZ + 同级 `_internal` 落盘文件）。

    PYZ 是权威来源：纯 Python 依赖只在这里。`_internal` 一起并入是为了兼容
    `--collect-all` 把包源码当数据复制的情形（实测 `edge_tts` 两边都有）。
    """
    from PyInstaller.archive.readers import CArchiveReader

    reader = CArchiveReader(str(exe))
    names: set[str] = set()
    for name, entry in reader.toc.items():
        typecode = entry[-1]
        if typecode == "z":  # 内嵌 PYZ
            embedded = reader.open_embedded_archive(name)
            names.update(embedded.toc.keys())
    names |= _disk_module_names(exe.parent / "_internal")
    return names


def missing_modules(present: set[str], required: set[str]) -> list[str]:
    """纯函数：返回缺失模块名（排序，便于稳定断言与阅读）。"""
    return sorted(required - present)


def find_exe(app_dir: Path) -> Path | None:
    """按平台产物布局找主程序：*.app（macOS）/ *.exe（Windows）/ 无后缀（Linux）。"""
    if app_dir.suffix.lower() == ".app":
        macos = app_dir / "Contents" / "MacOS"
        if macos.is_dir():
            candidates = [p for p in sorted(macos.iterdir()) if p.is_file()]
            if candidates:
                return candidates[0]
        return None
    exes = sorted(app_dir.glob("*.exe"))
    if exes:
        return exes[0]
    # Linux onedir：主程序与 _internal 同级，取唯一一个无后缀的普通文件。
    if (app_dir / "_internal").is_dir():
        candidates = [p for p in sorted(app_dir.iterdir()) if p.is_file() and not p.suffix]
        if candidates:
            return candidates[0]
    return None


def verify(app_dir: Path | None, exe: Path | None) -> tuple[int, dict]:
    """返回 (退出码, JSON 摘要)。"""
    if exe is None:
        if app_dir is None or not app_dir.is_dir():
            return 2, {"error": "app-dir-missing", "app_dir": str(app_dir)}
        exe = find_exe(app_dir)
        if exe is None or not exe.is_file():
            return 2, {"error": "exe-not-found", "app_dir": str(app_dir)}

    closure = import_closure()
    if closure is None:
        return 5, {
            "error": "edge-tts-not-installed-in-build-env",
            "hint": "pip install -r requirements.txt（缺依赖时 --collect-all edge_tts 只警告不报错，产物会静默缺 TTS）",
            "exe": str(exe),
        }
    required = set(MINIMUM_MODULES) | {m for m in closure if m == "edge_tts" or m.startswith("edge_tts.")}
    # 闭包里其余的第三方依赖（aiohttp / tabulate / certifi 及其传递依赖）也要在包里。
    required |= {m.split(".")[0] for m in closure}

    try:
        present = frozen_module_names(exe)
    except Exception as exc:  # noqa: BLE001 - 读不出清单必须中止，不得静默放行
        return 3, {"error": f"archive-read-failed: {type(exc).__name__}: {exc}", "exe": str(exe)}

    missing = missing_modules(present, required)
    summary = {
        "exe": str(exe),
        "frozen_modules": len(present),
        "required_modules": len(required),
        "missing": missing,
        "checked": sorted(required),
    }
    if missing:
        return 4, summary
    return 0, summary


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Verify the bundled edge-tts stack.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--app-dir", help="onedir 产物目录（或 macOS 的 .app）")
    group.add_argument("--exe", help="产物主程序路径")
    args = parser.parse_args(argv)

    code, summary = verify(
        Path(args.app_dir).resolve() if args.app_dir else None,
        Path(args.exe).resolve() if args.exe else None,
    )
    if code == 0:
        print(f"[tts] bundle TTS stack OK ({summary['required_modules']} modules required, {summary['frozen_modules']} frozen)")
    elif code in (4,):
        print(f"[tts] FAIL missing TTS modules: {', '.join(summary['missing'])}", file=sys.stderr)
    else:
        print(f"[tts] FAIL {summary.get('error')}", file=sys.stderr)
    print(json.dumps(summary, ensure_ascii=True))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
