#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""打包后桥接插件的零依赖校验与清理。

历史背景：桥接插件曾声明 @deepseek-ai/* 运行时依赖（issue: Cannot find
package '@deepseek-ai/cosmokit' / 'dsh-llm'）。PyInstaller 的 --add-data 会
把源码 integrations 目录连同 pnpm 的 junction 布局一起复制成损坏副本，
Cordis loader import bridge/index.js 时依赖解析失败，整个 DSH 插件树初始
化失败、DSH 无法启动（web/headless/desktop 全 profile，2026-09 事故）。

现状：桥接插件已改为零外部依赖（package.json 不声明 dependencies，
envelope 手写在 index.js 内，与 dsh createUserMessage 形状对齐）。本脚本
随之退化为两道防线：
  1. 剥离 dist 副本里可能存在的 node_modules（本机源码树残留 junk 被
     PyInstaller 原样复制时会带进产物）；
  2. 在 dist 副本上执行 hermetic 零依赖冒烟（verify_import.mjs 会把插件
     拷进无 node_modules 的临时目录再 import），任何外部 bare import
     都会让构建失败，而不是在用户机器上炸掉 dsh。

用法（在构建脚本的 PyInstaller 之后调用）：
    python scripts/fix_bridge_bundle.py --app-dir dist-onedir/dsh-pet-standalone-webm-chat
"""
import argparse
import json
import os
import shutil
import subprocess
import sys

REQUIRED_SMOKE = "verify_import.mjs"


def find_dist_bridge(app_dir: str) -> "str | None":
    """定位产物里的 bridge 目录。

    PyInstaller 的产物布局随平台不同：Windows/Linux 的 onedir 把数据放在
    ``<app>/_internal/``，macOS 的 ``.app`` 放在 ``Contents/Resources/``
    （部分版本在 ``Contents/Frameworks/``）。早期实现硬编码 ``_internal/``，
    导致 macOS 构建在「dist bridge missing」处失败。
    """
    candidates = (
        os.path.join(app_dir, "_internal", "integrations", "dsh-pet-bridge"),
        os.path.join(app_dir, "Contents", "Resources", "integrations", "dsh-pet-bridge"),
        os.path.join(app_dir, "Contents", "Frameworks", "integrations", "dsh-pet-bridge"),
        os.path.join(app_dir, "integrations", "dsh-pet-bridge"),
    )
    for path in candidates:
        if os.path.isdir(path):
            return path
    # 兜底：按目录名搜索，兼容未来 PyInstaller 的布局变化
    for root, _dirs, _files in os.walk(app_dir):
        if (os.path.basename(root) == "dsh-pet-bridge"
                and os.path.basename(os.path.dirname(root)) == "integrations"):
            return root
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--app-dir", required=True,
                    help="onedir/.app 输出目录（内含 integrations/dsh-pet-bridge）")
    args = ap.parse_args()

    # 归一为绝对路径：CI 以 `--dist dist`（相对）调用构建脚本并透传到此，
    # 若保持相对，下面 subprocess.run(..., cwd=dst_bridge) 会让 node 相对
    # 自己的 cwd 再拼一次 smoke 路径（双重嵌套 → Cannot find module，
    # Linux/macOS 构建红；Windows 走 build_onedir.ps1 绝对路径不受影响）。
    args.app_dir = os.path.abspath(args.app_dir)

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    src_bridge = os.path.join(root, "integrations", "dsh-pet-bridge")

    dst_bridge = find_dist_bridge(args.app_dir)
    if dst_bridge is None:
        print(f"[bridge] dist bridge missing under: {args.app_dir}", file=sys.stderr)
        return 1

    # 防线 0（不依赖 node，Linux/macOS 构建路径没有别的清单校验）：dist 副本的
    # 清单必须零依赖——dependencies/peerDependencies/optionalDependencies 任一
    # 有声明即判红。pnpm 的 link: 协议不安装被链接包自己的依赖，而链接目标常是
    # 这份无 node_modules 的打包副本。
    try:
        with open(os.path.join(dst_bridge, "package.json"), encoding="utf-8") as fh:
            dist_manifest = json.load(fh)
    except Exception as exc:
        print(f"[bridge] dist manifest unreadable: {exc}", file=sys.stderr)
        return 1
    declared = [
        name
        for field in ("dependencies", "peerDependencies", "optionalDependencies")
        for name in (dist_manifest.get(field) or {})
    ]
    if declared:
        print("[bridge] dist manifest declares runtime dependencies "
              f"(zero-dependency red line): {', '.join(sorted(declared))}",
              file=sys.stderr)
        return 1

    # 防线 1：dist 副本不允许带 node_modules。零依赖插件不需要它；若本机源码树
    # 残留着带 pnpm junction 的 node_modules，PyInstaller 会把链接原样复制进产物，
    # 在用户机器上变成悬空/损坏链接。它可能是普通目录、junction 或符号链接。
    dst_nm = os.path.join(dst_bridge, "node_modules")
    if os.path.islink(dst_nm) or os.path.isdir(dst_nm):
        if os.path.islink(dst_nm) and not os.path.isdir(dst_nm):
            os.unlink(dst_nm)
        else:
            shutil.rmtree(dst_nm)
        print(f"[bridge] stripped node_modules from bundle copy: {dst_nm}")

    # 防线 2：hermetic 冒烟。脚本位于 bridge 目录内，会把插件拷进无
    # node_modules 的临时目录再 import，验证零依赖不变量 + envelope 形状。
    smoke = os.path.join(dst_bridge, REQUIRED_SMOKE)
    src_smoke = os.path.join(src_bridge, REQUIRED_SMOKE)
    if not os.path.exists(smoke):
        if not os.path.exists(src_smoke):
            print(f"[bridge] missing verify script: {src_smoke}", file=sys.stderr)
            return 1
        shutil.copy2(src_smoke, smoke)

    node = shutil.which("node")
    if node:
        print("[bridge] running zero-dependency import smoke on bundle copy...")
        result = subprocess.run([node, smoke], cwd=dst_bridge)
        if result.returncode != 0:
            print("[bridge] zero-dependency smoke FAILED - bundle bridge violates "
                  "the red line", file=sys.stderr)
            return 1
    else:
        # 构建机没有 node 时无法执行冒烟；PR 门禁（node --test）仍会拦截，
        # 这里只提示不判失败，保持本地无 node 环境可构建。
        print("[bridge] node not found - smoke skipped (PR gate covers it)")

    print("[bridge] zero-dependency bundle OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
