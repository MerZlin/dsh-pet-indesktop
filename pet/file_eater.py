# -*- coding: utf-8 -*-
"""“吃垃圾文件”模拟功能：拖文件给桌宠后播放吃动画并记录统计。

重要边界：本模块只做视觉反馈与统计，绝不删除/移动/修改被拖入的文件。
统计写到配置目录下的 file_eaten_stats.json，供用户查看累计吃了多少文件。
"""
from __future__ import annotations

import json
import os
import random
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QEvent, QObject
from PySide6.QtCore import QUrl

STATS_FILE_NAME = "file_eaten_stats.json"
STATS_HISTORY_LIMIT = 50

# 吃动画候选关键词；找不到时使用普通随机动作兜底。
EAT_KEYWORDS = ("吃", "eat", "啃")
# 如果角色恰好有这些更贴近“吃文件/吃数字垃圾”的动画，优先播放。
PREFERRED_EAT_ANIMATIONS = ("吃Token", "大口吃零食", "偷吃零食被抓住")


def _default_stats() -> dict:
    return {
        "feed_count": 0,
        "file_count": 0,
        "folder_count": 0,
        "item_count": 0,
        "total_bytes": 0,
        "history": [],
    }


def _load_stats(path: Path) -> dict:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _default_stats()
    if not isinstance(raw, dict):
        return _default_stats()
    stats = _default_stats()
    for key in ("feed_count", "file_count", "folder_count", "item_count", "total_bytes"):
        try:
            stats[key] = int(raw.get(key, 0))
        except (TypeError, ValueError):
            stats[key] = 0
    history = raw.get("history")
    if isinstance(history, list):
        stats["history"] = history[-STATS_HISTORY_LIMIT:]
    return stats


def _save_stats(path: Path, stats: dict) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        tmp.write_text(
            json.dumps(stats, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(tmp, path)
    except OSError:
        # 统计只是附加功能：写盘失败不能影响“吃”这个交互本身。
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def format_bytes(size: int) -> str:
    size = max(0, int(size))
    if size < 1024:
        return f"{size} B"
    if size < 1024 ** 2:
        return f"{size / 1024:.1f} KB"
    if size < 1024 ** 3:
        return f"{size / 1024 ** 2:.1f} MB"
    return f"{size / 1024 ** 3:.1f} GB"


def _path_size(path: Path) -> int:
    """文件直接返回大小；目录递归累计大小，但不展开成多个“文件数”。"""
    try:
        if path.is_file():
            return path.stat().st_size
        total = 0
        for root, _dirs, files in os.walk(path):
            for name in files:
                try:
                    total += (Path(root) / name).stat().st_size
                except OSError:
                    continue
        return total
    except OSError:
        return 0


def _measure_path(path: Path) -> tuple[int, int, int]:
    """返回 (文件数, 文件夹数, 字节数)。"""
    try:
        if path.is_file():
            return 1, 0, _path_size(path)
        if path.is_dir():
            return 0, 1, _path_size(path)
    except OSError:
        pass
    return 0, 0, 0


def _pick_eating_animation(pet) -> str | None:
    """从当前角色的随机动作池里选一个吃相关动画。"""
    acts = list(getattr(pet, "acts", None) or [])
    if not acts:
        return None
    for preferred in PREFERRED_EAT_ANIMATIONS:
        if preferred in acts:
            return preferred
    candidates = [
        name for name in acts
        if any(keyword in name for keyword in EAT_KEYWORDS)
    ]
    return random.choice(candidates) if candidates else random.choice(acts)


class FileEaterDropHandler(QObject):
    """挂在桌宠窗口上的拖放事件过滤器 + 吃文件反馈控制器。"""

    def __init__(self, pet):
        super().__init__(pet)
        self.pet = pet
        cfg_dir = getattr(getattr(pet, "cfg", None), "dir", None)
        self.stats_path = Path(cfg_dir) / STATS_FILE_NAME if cfg_dir else None
        pet.setAcceptDrops(True)
        pet.installEventFilter(self)

    # ------------------------------------------------------------ 事件
    def _local_paths(self, mime) -> list[str]:
        if not mime.hasUrls():
            return []
        return [
            url.toLocalFile()
            for url in mime.urls()
            if isinstance(url, QUrl) and url.isLocalFile() and url.toLocalFile()
        ]

    def eventFilter(self, watched, event):  # noqa: N802 (Qt 命名)
        if watched is not self.pet:
            return False
        event_type = event.type()
        if event_type in (QEvent.Type.DragEnter, QEvent.Type.DragMove):
            if self._local_paths(event.mimeData()):
                event.acceptProposedAction()
                return True
            return False
        if event_type == QEvent.Type.Drop:
            paths = self._local_paths(event.mimeData())
            if paths:
                self.eat_paths(paths)
                event.acceptProposedAction()
                return True
            return False
        return False

    # ------------------------------------------------------------ 反馈
    def eat_paths(self, paths) -> dict:
        """处理一批本地路径：统计、播放动画、气泡提示；文件保持不动。"""
        files = folders = 0
        total_bytes = 0
        for raw in paths:
            path = Path(raw)
            file_count, folder_count, size = _measure_path(path)
            files += file_count
            folders += folder_count
            total_bytes += size

        stats = self._record(files, folders, files + folders, total_bytes)
        self._play_eating_animation()
        self._show_feedback(files, folders, total_bytes, stats)
        return {
            "files": files,
            "folders": folders,
            "bytes": total_bytes,
            "stats": stats,
        }

    def _record(self, files: int, folders: int, items: int, total_bytes: int) -> dict:
        if self.stats_path is None:
            return _default_stats()
        stats = _load_stats(self.stats_path)
        stats["feed_count"] += 1
        stats["file_count"] += max(0, files)
        stats["folder_count"] += max(0, folders)
        stats["item_count"] += max(0, items)
        stats["total_bytes"] += max(0, total_bytes)
        stats["history"].append({
            "time": datetime.now().isoformat(timespec="seconds"),
            "files": max(0, files),
            "folders": max(0, folders),
            "bytes": max(0, total_bytes),
        })
        del stats["history"][:-STATS_HISTORY_LIMIT]
        _save_stats(self.stats_path, stats)
        return stats

    def _play_eating_animation(self) -> None:
        name = _pick_eating_animation(self.pet)
        if not name:
            return
        request = getattr(self.pet, "request_link_anim", None)
        if callable(request):
            request(name)
            return
        switch = getattr(self.pet, "switch_clip", None)
        if callable(switch):
            switch(name, link_request=True)

    def _show_feedback(self, files: int, folders: int, total_bytes: int, stats: dict) -> None:
        show = getattr(self.pet, "show_bubble", None)
        if not callable(show):
            return
        if files and folders:
            batch = f"{files} 个文件、{folders} 个文件夹"
        elif files:
            batch = f"{files} 个文件"
        elif folders:
            batch = f"{folders} 个文件夹"
        else:
            batch = "空气"
        text = (
            f"啊呜～吃掉 {batch}（{format_bytes(total_bytes)}），"
            f"累计吃掉 {stats.get('file_count', 0)} 个文件、"
            f"{stats.get('folder_count', 0)} 个文件夹，"
            f"共 {format_bytes(stats.get('total_bytes', 0))}！"
        )
        show(text, subtitle="放心，只是做个样子，文件没有删除或移动哦")
