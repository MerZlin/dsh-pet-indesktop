# -*- coding: utf-8 -*-
"""onedir 产物瘦身（构建期调用，见 scripts/build_onedir.ps1 §slim）。

只做「移除」，不改写任何被打包内容；移除项全部来自白名单，且必须先过两道硬校验：

1. 依赖闭包校验（pefile）：若某个「保留二进制」（dll/pyd/exe）静态 import 了
   待移除文件，立即中止——说明该模块仍在运行链上，不能删；
2. 必需清单校验：瘦身前/后核心运行文件（Qt 核心、平台插件、ffmpeg 多媒体
   插件、Python 绑定、shiboken6、exe）必须齐全，缺一个即中止。

白名单依据（2026-09-15 逐项核对：pefile 依赖图 + 全仓代码引用检索）：

- Qt Quick / QML / VirtualKeyboard 栈（Qt6Quick*/Qt6Qml*/Qt6VirtualKeyboard*/
  Qt6OpenGL.dll + plugins/platforminputcontexts + qml/ 目录）：
  桌宠为纯 QtWidgets + QtMultimedia 应用，全仓无 QtQuick/QtQml/QVirtualKeyboard
  使用；依赖图上 Qt6Quick 仅被 Qt6VirtualKeyboard 引用，Qt6VirtualKeyboard 仅被
  qtvirtualkeyboardplugin 引用，而该插件只在 QT_IM_MODULE=qtvirtualkeyboard 时
  由 Qt 加载（本项目从不设置）。
- QtPdf（Qt6Pdf*.dll + plugins/imageformats/qpdf.dll）：无任何 PDF 代码路径。
- opengl32sw.dll（Mesa 软件 OpenGL 后备）：全 bundle 零静态引用；本项目无
  QOpenGLWidget/QtQuick，Windows 上 Qt Widgets 走 raster、QVideoSink 走
  D3D11（含 WARP 软件后备），运行时不加载该 DLL。可用 --keep-opengl-sw 保留。
- Qt 翻译：仅保留 zh_CN / zh_TW / en（界面为中文，其余 100+ 语言包无引用）。
- PIL AVIF 插件（_internal/PIL/*avif*）：全仓无 AVIF 图片使用。
- OpenCV 的 FFMPEG 视频后端（_internal/cv2/opencv_videoio_ffmpeg<版本>_64.dll，
  本机实测 27.3 MB）：本地 OCR 只用 cv2 的图像算子（颜色转换、缩放、形态学），
  **不碰视频 I/O**；该 DLL 由 cv2.pyd 在首次 VideoCapture/VideoWriter 时动态加载
  （pefile 实测 cv2.pyd 的导入表里没有它，所以依赖闭包校验会放行）。文件名带
  OpenCV 版本号，所以白名单用通配 `opencv_videoio_ffmpeg*.dll`——换 opencv 版本
  不需要同步改这里。

stdout 最后一行打印 JSON 摘要（removed_count / freed_bytes / removed / skipped），
供构建脚本记录体积对比；所有日志为 ASCII，避免 GBK 控制台乱码。
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import sys
from pathlib import Path

PYSIDE = "_internal/PySide6"
TRANSLATION_DIR = f"{PYSIDE}/translations"
TRANSLATION_KEEP = ("_zh_CN", "_zh_TW", "_en")

# 白名单：相对 app_dir 的 glob（匹配时统一小写，Windows 大小写不敏感）
REMOVAL_GLOBS = (
    f"{PYSIDE}/Qt6Quick*.dll",
    f"{PYSIDE}/Qt6Qml*.dll",
    f"{PYSIDE}/Qt6VirtualKeyboard*.dll",
    f"{PYSIDE}/Qt6OpenGL.dll",
    f"{PYSIDE}/Qt6Pdf*.dll",
    f"{PYSIDE}/qml/**",
    f"{PYSIDE}/plugins/platforminputcontexts/*",
    f"{PYSIDE}/plugins/imageformats/qpdf.dll",
    f"{PYSIDE}/opengl32sw.dll",
    "_internal/PIL/*avif*",
    "_internal/cv2/opencv_videoio_ffmpeg*.dll",
)

# 违禁清单：构建机环境污染（在打包机装了但产品零引用的包）一旦被连带收集，
# 直接判构建失败——不许靠人工 review dist 才发现（2026-10-01 实测：部署包
# _internal 里 django 30MB + numpy 5.9MB + numpy.libs 21MB）。
#
# **numpy 已于 2026-10-13 移出本清单**：快速识屏的本地 OCR 直接 import numpy
# （``pet/screen_capture/ocr.py`` 的 QImage → BGR 数组），它不再是"打包机污染"
# 而是**随包必带的运行时依赖**（requirements.txt 里也已声明）。留在清单里的后果
# 不是"更干净"而是硬闸误杀：每次构建都会红在一个完全正常的产物上。
# 移除它时同时删掉了 ``build_onedir.ps1`` 里的 ``--exclude-module numpy``——
# 两处一致，正是那条注释原本要求的"换机/新装库后复查"。
# django / hypothesis 仍然零引用，继续禁：防线是双层的——build_onedir.ps1 用
# --exclude-module 让 PyInstaller 不收集，这里兜底，任何路径把它们带进产物
# （新依赖、新 hook、换机）都会红。
FORBIDDEN_GLOBS = (
    "_internal/django/**",
    "_internal/hypothesis/**",
)


def find_forbidden(rel_paths) -> list[str]:
    """bundle 相对路径列表 → 命中违禁清单的路径（空 = 干净）。"""
    hits: list[str] = []
    for rel in rel_paths:
        low = _norm(rel).lower()
        if any(fnmatch.fnmatch(low, pat.lower()) for pat in FORBIDDEN_GLOBS):
            hits.append(_norm(rel))
    return hits

# 必需清单：瘦身前/后都必须存在（glob，覆盖版本号变化）
REQUIRED_GLOBS = (
    "*.exe",
    f"{PYSIDE}/Qt6Core.dll",
    f"{PYSIDE}/Qt6Gui.dll",
    f"{PYSIDE}/Qt6Widgets.dll",
    f"{PYSIDE}/Qt6Network.dll",
    f"{PYSIDE}/Qt6Multimedia.dll",
    f"{PYSIDE}/Qt6Svg.dll",
    f"{PYSIDE}/QtCore.pyd",
    f"{PYSIDE}/QtGui.pyd",
    f"{PYSIDE}/QtWidgets.pyd",
    f"{PYSIDE}/QtMultimedia.pyd",
    f"{PYSIDE}/pyside6.abi3.dll",
    f"{PYSIDE}/avcodec-*.dll",
    f"{PYSIDE}/plugins/platforms/qwindows.dll",
    f"{PYSIDE}/plugins/multimedia/ffmpegmediaplugin.dll",
    # 内置网页 AI（tool.ai-browser 的 chat_view）：PyInstaller 没有
    # hook-PySide6.QtWebView，这两个文件靠 build_onedir.ps1 显式带进包；
    # 缺任何一个，冻结包里的「网页 AI」按钮就是一个点了必报错的死入口。
    f"{PYSIDE}/Qt6WebView.dll",
    f"{PYSIDE}/plugins/webview/qtwebview_webview2.dll",
    "_internal/shiboken6/*.dll",
)


def _norm(rel: str) -> str:
    return rel.replace("\\", "/")


def _rel(app_dir: Path, path: Path) -> str:
    return _norm(os.path.relpath(str(path), str(app_dir)))


def plan_removals(rel_paths, *, keep_opengl_sw: bool = False):
    """纯函数：bundle 内相对路径列表 → (待移除列表, 显式跳过列表)。"""
    removals: list[str] = []
    skipped: list[str] = []
    for rel in rel_paths:
        item = _norm(rel)
        low = item.lower()
        if low.endswith("/opengl32sw.dll") or low == "opengl32sw.dll":
            if keep_opengl_sw:
                skipped.append(item)
                continue
            removals.append(item)
            continue
        if "/" in item and item.rsplit("/", 1)[0].lower() == TRANSLATION_DIR.lower():
            name = item.rsplit("/", 1)[-1]
            if not any(key in name for key in TRANSLATION_KEEP):
                removals.append(item)
            continue
        if any(fnmatch.fnmatch(low, pat.lower()) for pat in REMOVAL_GLOBS):
            removals.append(item)
    return removals, skipped


def collect_bundled_binaries(app_dir: Path) -> dict[str, Path]:
    found: dict[str, Path] = {}
    for root, _dirs, files in os.walk(app_dir):
        for name in files:
            if name.lower().endswith((".dll", ".pyd", ".exe")):
                path = Path(root) / name
                found[_rel(app_dir, path)] = path
    return found


def _read_imports(path: Path) -> set[str]:
    import pefile  # 随 PyInstaller 安装；缺失即构建失败（不静默降级）

    names: set[str] = set()
    pe = pefile.PE(str(path), fast_load=True)
    try:
        pe.parse_data_directories(
            directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_IMPORT"]]
        )
        for entry in getattr(pe, "DIRECTORY_ENTRY_IMPORT", []) or []:
            names.add(entry.dll.decode("latin1").lower())
    finally:
        pe.close()
    return names


def verify_no_live_reference(binaries, removals, read_imports=_read_imports):
    """返回 [(保留文件, [被它引用的待移除文件名]), ...]；空列表 = 通过。"""
    removal_set = {_norm(r).lower() for r in removals}
    removal_names = {_norm(r).rsplit("/", 1)[-1].lower() for r in removals}
    conflicts = []
    for rel, path in binaries.items():
        if _norm(rel).lower() in removal_set:
            continue
        deps = read_imports(path)  # 解析失败向上抛出（中止瘦身）
        hit = sorted(deps & removal_names)
        if hit:
            conflicts.append((_norm(rel), hit))
    return conflicts


def verify_required(rel_paths, required=REQUIRED_GLOBS) -> list[str]:
    lows = [_norm(r).lower() for r in rel_paths]
    missing = []
    for pat in required:
        low_pat = pat.lower()
        if not any(fnmatch.fnmatch(rel, low_pat) for rel in lows):
            missing.append(pat)
    return missing


def prune_empty_dirs(app_dir: Path) -> int:
    """清理瘦身后留下的空目录（仅 app_dir 内部，不越界）。"""
    removed = 0
    for root, dirs, _files in os.walk(app_dir, topdown=False):
        for name in dirs:
            target = Path(root) / name
            try:
                if not any(target.iterdir()):
                    target.rmdir()
                    removed += 1
            except OSError:
                pass
    return removed


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Slim a PyInstaller onedir bundle.")
    parser.add_argument("--app-dir", required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--keep-opengl-sw", action="store_true")
    args = parser.parse_args(argv)

    app_dir = Path(args.app_dir).resolve()
    if not app_dir.is_dir():
        print(f"[slim] FAIL app dir missing: {app_dir}", file=sys.stderr)
        return 2

    before = [_rel(app_dir, p) for p in app_dir.rglob("*") if p.is_file()]
    forbidden = find_forbidden(before)
    if forbidden:
        print("[slim] FAIL bundle contains build-env pollution (product has zero imports; "
              "fix the build env or the exclude list): " + ", ".join(forbidden[:10]),
              file=sys.stderr)
        return 6
    missing_before = verify_required(before)
    if missing_before:
        print("[slim] FAIL bundle incomplete before slimming: " + ", ".join(missing_before), file=sys.stderr)
        return 3

    removals, skipped = plan_removals(before, keep_opengl_sw=args.keep_opengl_sw)
    binaries = collect_bundled_binaries(app_dir)
    try:
        # 显式从模块全局取（而非依赖默认参数绑定），保持可注入以便单测替换。
        conflicts = verify_no_live_reference(binaries, removals, read_imports=_read_imports)
    except Exception as exc:  # noqa: BLE001 - 校验能力缺失必须中止，不得静默放行
        print(f"[slim] FAIL dependency closure check unavailable: {exc}", file=sys.stderr)
        return 4
    if conflicts:
        for rel, hit in conflicts[:10]:
            print(f"[slim] REFUSED {rel} still imports: {', '.join(hit)}", file=sys.stderr)
        print("[slim] FAIL dependency closure check - nothing removed", file=sys.stderr)
        return 5

    freed = 0
    removed: list[str] = []
    for rel in removals:
        path = app_dir / rel
        try:
            size = path.stat().st_size
        except OSError:
            continue
        freed += size
        if args.dry_run:
            removed.append(rel)
            continue
        try:
            path.unlink()
        except OSError:
            # Windows 常见两类失败：只读属性（跨环境复制）与活进程锁定 DLL。
            # 清只读位后重试一次；仍失败则带提示中止，不静默产出残缺包。
            try:
                os.chmod(path, 0o666)
                path.unlink()
            except OSError as exc:
                hint = (
                    " (locked by a running process; close the app and retry)"
                    if getattr(exc, "winerror", None) == 5
                    else ""
                )
                print(f"[slim] FAIL remove {rel}: {exc}{hint}", file=sys.stderr)
                return 6
        removed.append(rel)

    if not args.dry_run:
        prune_empty_dirs(app_dir)

    after = [_rel(app_dir, p) for p in app_dir.rglob("*") if p.is_file()] if not args.dry_run else [
        r for r in before if r not in set(removals)
    ]
    missing_after = verify_required(after)
    if missing_after:
        print("[slim] FAIL required files missing after slimming: " + ", ".join(missing_after), file=sys.stderr)
        return 7

    summary = {
        "mode": "dry-run" if args.dry_run else "applied",
        "removed_count": len(removed),
        "freed_bytes": freed,
        "freed_mb": round(freed / 1048576, 2),
        "kept_opengl_sw": bool(args.keep_opengl_sw),
        "skipped_count": len(skipped),
        "removed": sorted(removed),
    }
    text = json.dumps(summary, ensure_ascii=True)
    print(f"[slim] removed {summary['removed_count']} files, freed {summary['freed_mb']} MB")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
