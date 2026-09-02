# -*- coding: utf-8 -*-
"""P2 内存预算审计：首帧预热缓存 / meta 缓存总量是否有界？（批12）

实测一个「预热完成」的实例占用多少内存，并量化各缓存的上界形态：
- WebMClip._first_image（每 clip 一张首帧 QImage，warm_first_frame 缓存）
- WebMClip._current_*（播放中额外持有的 QPixmap）
- webm_clip._META_CACHE（进程内元数据缓存，key=(path|mtime|size)）
- webm_clip._META_FILE_CACHE / 磁盘 JSON（跨进程共享元数据缓存）
- MovieLibrary._movies（clip 对象表）

用法：.venv\\Scripts\\python.exe scripts/audit_warm_cache.py
输出：打印汇总 + 写 _plan/current/AUDIT_WARM_CACHE_DATA.json。

注意：脚本会真实拉起 ffmpeg 预热全部动画首帧（与生产预热同一路径），
耗时数分钟级；审计用独立的临时 meta 缓存文件，不污染真实缓存。
"""
from __future__ import annotations

import ctypes
import gc
import json
import os
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))

from PySide6.QtWidgets import QApplication  # noqa: E402

from pet import webm_clip  # noqa: E402
from pet.library import MovieLibrary  # noqa: E402

OUT = _REPO / "_plan" / "current" / "AUDIT_WARM_CACHE_DATA.json"


def _rss_bytes() -> int:
    """当前进程 RSS（Windows 用 psapi；其它平台尽力而为，失败返回 0）。"""
    try:
        if os.name == "nt":
            from ctypes import wintypes

            class _PROCESS_MEMORY_COUNTERS(ctypes.Structure):
                _fields_ = [
                    ("cb", wintypes.DWORD),
                    ("PageFaultCount", wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t),
                ]

            psapi = ctypes.windll.psapi
            kernel32 = ctypes.windll.kernel32
            kernel32.GetCurrentProcess.restype = wintypes.HANDLE
            psapi.GetProcessMemoryInfo.argtypes = [
                wintypes.HANDLE,
                ctypes.POINTER(_PROCESS_MEMORY_COUNTERS),
                wintypes.DWORD,
            ]
            counters = _PROCESS_MEMORY_COUNTERS()
            counters.cb = ctypes.sizeof(_PROCESS_MEMORY_COUNTERS)
            if psapi.GetProcessMemoryInfo(
                kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb
            ):
                return int(counters.WorkingSetSize)
            return 0
        with open("/proc/self/statm", encoding="ascii") as f:
            parts = f.read().split()
        return int(parts[1]) * os.sysconf("SC_PAGE_SIZE")
    except Exception:
        return 0


def _img_bytes(img) -> int:
    return img.sizeInBytes() if img is not None else 0


def _pm_bytes(pm) -> int:
    if pm is None or pm.isNull():
        return 0
    probe = getattr(pm, "sizeInBytes", None)
    if callable(probe):
        try:
            return int(probe())
        except Exception:
            pass
    try:
        return pm.toImage().sizeInBytes()
    except Exception:
        return pm.width() * pm.height() * 4


def _mb(n: int) -> str:
    return f"{n / 1024 / 1024:.2f} MB"


def warm_all(lib: MovieLibrary, workers: int = 4) -> list:
    """创建全部 clip 并完成 meta + 首帧预热（生产同一批 API）。"""
    clips = [lib.movie(name) for name in lib.names()]
    with ThreadPoolExecutor(max_workers=workers) as ex:
        list(ex.map(lambda c: c.warm_meta(), clips))
        list(ex.map(lambda c: getattr(c, "warm_first_frame", lambda: None)(), clips))
    return clips


def meta_file_cache_info() -> dict:
    entries = len(webm_clip._get_meta_file_cache())
    size = 0
    try:
        size = webm_clip._META_FILE_CACHE_PATH.stat().st_size
    except OSError:
        pass
    return {"entries": entries, "file_bytes": size}


def audit_character(char_id: str, tmp_dir: Path) -> dict:
    # 冷启动：把磁盘 meta 缓存指到独立临时文件，量的是本实例从零累积
    webm_clip._META_FILE_CACHE_PATH = tmp_dir / f"meta-{char_id}.json"
    webm_clip._META_FILE_CACHE = None
    webm_clip._META_CACHE.clear()

    rss0 = _rss_bytes()
    t0 = time.monotonic()

    lib = MovieLibrary(character_id=char_id)
    clips = warm_all(lib)
    t_warm = time.monotonic() - t0

    meta_entries = len(webm_clip._META_CACHE)
    meta_key_bytes = sum(len(k) for k in webm_clip._META_CACHE)
    file_info = meta_file_cache_info()

    first_images = [c._first_image for c in clips if c._first_image is not None]
    total_first_bytes = sum(_img_bytes(i) for i in first_images)
    # 播放中的 clip 还持有 QPixmap 形态（当前帧），单独量一份
    pm_bytes = sum(
        _pm_bytes(getattr(c, "_current_pixmap", None))
        for c in clips
    )

    # 真实播放姿态：把第一个 clip 应用到首帧（jumpToFrame(0) → 主线程转
    # QPixmap），量单 clip 在显示路径上额外持有的像素缓冲。
    displayed = clips[0]
    try:
        displayed.jumpToFrame(0)
        active_pm_bytes = _pm_bytes(displayed._current_pixmap)
    except Exception:
        active_pm_bytes = 0

    rss1 = _rss_bytes()

    # 素材原地更新的 churn 探针（修正版）：已在库里的 clip 元数据已就绪、
    # _ensure_meta 会短路不再重探——真实增长发生在「文件更新 + 新实例/
    # 新进程/新 clip」时。探针每轮 touch 一组文件，然后为每个被 touch 的
    # 文件新建一个全新 WebMClip 并 warm_meta（duration==0 → 真重探），
    # 验证 _META_CACHE / 文件缓存随「文件版本数 × 触达 clip 数」单调增长
    # 且永不逐出。探针结束后恢复原始 mtime（不污染真实素材的缓存依据）。
    sample = clips[:10]
    orig_mtimes = []
    for clip in sample:
        try:
            st = os.stat(clip.path)
            orig_mtimes.append((clip.path, st.st_atime_ns, st.st_mtime_ns))
        except OSError:
            orig_mtimes.append((clip.path, None, None))
    churn_keys = []
    churn_file_entries = []
    try:
        for _ in range(3):
            now = time.time()
            for clip in sample:
                try:
                    os.utime(clip.path, (now, now))
                except OSError:
                    pass
            fresh = [webm_clip.WebMClip(str(p)) for p, _, _ in orig_mtimes]
            with ThreadPoolExecutor(max_workers=4) as ex:
                list(ex.map(lambda c: c.warm_meta(), fresh))
            churn_keys.append(len(webm_clip._META_CACHE))
            info = meta_file_cache_info()
            churn_file_entries.append(info["entries"])
    finally:
        for path, atime_ns, mtime_ns in orig_mtimes:
            if atime_ns is None:
                continue
            try:
                os.utime(path, ns=(atime_ns, mtime_ns))
            except OSError:
                pass

    result = {
        "character": char_id,
        "clip_count": len(clips),
        "first_image_ready": len(first_images),
        "first_image_total_bytes": total_first_bytes,
        "first_image_total_mb": round(total_first_bytes / 1024 / 1024, 2),
        "first_image_per_clip_mb": round(
            total_first_bytes / max(1, len(first_images)) / 1024 / 1024, 3
        ),
        "qpixmap_extra_bytes": pm_bytes,
        "active_display_pixmap_bytes": active_pm_bytes,
        "warm_seconds": round(t_warm, 1),
        "rss_before_mb": round(rss0 / 1024 / 1024, 1),
        "rss_after_warm_mb": round(rss1 / 1024 / 1024, 1),
        "meta_cache_entries_after_warm": meta_entries,
        "meta_cache_key_bytes": meta_key_bytes,
        "file_cache_entries": file_info["entries"],
        "file_cache_bytes": file_info["file_bytes"],
        "churn_sample_clips": len(sample),
        "meta_cache_entries_per_churn_round": churn_keys,
        "file_cache_entries_per_churn_round": churn_file_entries,
    }

    # 释放探针：整库丢弃后 QImage 缓存是否可回收（只记数值，Qt 父子清理
    # 依赖应用层 deleteLater 时序，此处仅展示趋势）
    for c in clips:
        try:
            c._first_image = None
        except Exception:
            pass
    del lib
    del clips
    gc.collect()
    rss2 = _rss_bytes()
    result["rss_after_drop_refs_mb"] = round(rss2 / 1024 / 1024, 1)
    return result


def main() -> int:
    _ = QApplication.instance() or QApplication([])
    tmp_dir = Path(tempfile.mkdtemp(prefix="dsh-pet-audit-"))
    results = []
    chars = sorted(p.name for p in (Path("assets") / "characters").iterdir() if p.is_dir())
    for char_id in chars:
        print(f"[audit] warming character: {char_id}")
        results.append(audit_character(char_id, tmp_dir))
        print(json.dumps(results[-1], ensure_ascii=False, indent=2))
    try:
        OUT.write_text(
            json.dumps({"probe_time": time.strftime("%Y-%m-%d %H:%M:%S"), "results": results},
                       ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"[audit] data written to {OUT}")
    except OSError as exc:
        print(f"[audit] warn: 结果落盘失败 {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
