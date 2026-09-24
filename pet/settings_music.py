# -*- coding: utf-8 -*-
"""设置页「音乐播放器路径」：控件创建 / 行装配 / 保存 / 后台自动检测。

**为什么单独一个模块**：``modern_settings_dialog.py`` 的行数预算已无余量，
本组控件与行全部在此构建（与 ``settings_file_interpret.py`` 同口径）。

**为什么需要这个设置**：右键菜单「音乐 → 打开网易云/QQ音乐给主人放歌」需要知道
播放器装在哪。``pet/music_players.py`` 会先自动搜常见目录，但实测本机网易云在
``D:\\CloudMusic``、QQ音乐在 ``D:\\QQ音乐\\QQMusic``，都不在默认位置；搜不到时菜单项
置灰，提示是「可在配置文件中手动指定路径」——在本次之前**没有任何 UI 能写这个键**
（``music_players.clear_cache`` 的 docstring 一直登记着这个缺口）。

**自动检测为什么这么写**：目录浅扫实测 6s+，绝不能放 GUI 线程（曾把菜单冻住）。
这里复用 ``music_players`` 自己的后台预热与只读缓存：点按钮 → 幂等触发预热
→ 定时轮询只读缓存 → 出结果就回填并弹一次说明。轮询超时（默认 20s）也会收尾，
不会留一个永远转的定时器。
"""

from __future__ import annotations

import logging
import time

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QMessageBox, QPushButton

from . import music_players
from .settings_widgets import ResourcePathPicker, SettingRow

log = logging.getLogger("dsh-pet-standalone")

PLAYER_KEYS = ("netease", "qqmusic")
EXE_NAME_FILTER = "可执行文件 (*.exe);;所有文件 (*)"
DETECT_POLL_MS = 300
DETECT_TIMEOUT_SECONDS = 20.0
_PICKER_ATTR = {
    "netease": "music_player_netease_picker",
    "qqmusic": "music_player_qqmusic_picker",
}
_ROW_KEY = {
    "netease": "music_player_netease",
    "qqmusic": "music_player_qqmusic",
}

_DETECT_BUTTON_LABEL = "自动检测"


def create_music_player_controls(dialog) -> None:
    """创建两个路径选择器与「自动检测」按钮（幂等）。"""
    if getattr(dialog, "music_player_detect_btn", None) is not None:
        return
    configured = dialog.config.get("music_player_paths", {})
    configured = configured if isinstance(configured, dict) else {}
    for key in PLAYER_KEYS:
        label = music_players.player_label(key)
        picker = ResourcePathPicker(
            str(configured.get(key, "") or ""),
            name_filter=EXE_NAME_FILTER,
            dialog_title=f"选择{label}的程序",
            parent=dialog,
        )
        setattr(dialog, _PICKER_ATTR[key], picker)
    button = QPushButton(_DETECT_BUTTON_LABEL, dialog)
    button.setObjectName("musicPlayerDetectButton")
    button.clicked.connect(lambda: start_detect(dialog))
    dialog.music_player_detect_btn = button


def build_music_player_rows(dialog) -> list[SettingRow]:
    """本组的设置行（由「桌宠 · 音乐关联」组直接取用，不走 claim）。"""
    rows: list[SettingRow] = []
    for key in PLAYER_KEYS:
        label = music_players.player_label(key)
        rows.append(
            SettingRow(
                _ROW_KEY[key],
                f"{label}程序",
                f"{label}的安装位置（指向可执行文件）。留空 = 自动搜索常见安装目录；填了就以它为准（文件不存在时右键菜单会提示找不到，而不是偷偷回退）。",
                getattr(dialog, _PICKER_ATTR[key]),
                stacked=True,
            )
        )
    rows.append(
        SettingRow(
            "music_player_detect",
            "自动检测播放器",
            "在后台按常见目录名搜一遍（几个盘符 + 用户目录，最多三层），找到就填进上面两行；搜索在后台做，不会卡住设置页。",
            dialog.music_player_detect_btn,
        )
    )
    return rows


def save_music_player_settings(dialog) -> None:
    """写回 ``music_player_paths``；两项都留空 = 回到自动搜索，并清掉路径缓存。"""
    if getattr(dialog, "music_player_detect_btn", None) is None:
        return
    paths = {}
    for key in PLAYER_KEYS:
        value = getattr(dialog, _PICKER_ATTR[key]).text().strip()
        if value:
            paths[key] = value
    previous = dialog.config.get("music_player_paths", {})
    if not isinstance(previous, dict):
        previous = {}
    dialog.config.set("music_player_paths", paths)
    if paths != previous:
        # 路径变了：让右键菜单下次拿到确定态（自动搜索的负缓存也要作废）。
        music_players.clear_cache()


def start_detect(dialog, *, warm=None, lookup=None) -> None:
    """「自动检测」入口：后台预热 + 定时轮询回填（GUI 线程只读缓存，不扫盘）。"""
    warm = warm or music_players.warm_cache_async
    dialog._music_detect_lookup = lookup
    dialog._music_detect_deadline = time.monotonic() + DETECT_TIMEOUT_SECONDS
    dialog._music_detect_results = {}
    for key in PLAYER_KEYS:
        warm(key, "")

    button = dialog.music_player_detect_btn
    button.setEnabled(False)
    button.setText("检测中…")

    timer = getattr(dialog, "_music_detect_timer", None)
    if timer is None:
        timer = QTimer(dialog)
        timer.setInterval(DETECT_POLL_MS)
        timer.timeout.connect(lambda: poll_detect(dialog))
        dialog._music_detect_timer = timer
    timer.start()
    poll_detect(dialog)


def poll_detect(dialog) -> None:
    """轮询一次：有冷缓存就继续等，出结果（或超时）就回填并收尾。"""
    lookup = getattr(dialog, "_music_detect_lookup", None) or music_players.cached_player
    results: dict[str, str] = getattr(dialog, "_music_detect_results", {})
    pending = False
    for key in PLAYER_KEYS:
        state, path = lookup(key, "")
        if path:
            results[key] = path
        elif state == music_players.CACHED_COLD:
            pending = True
    if pending and time.monotonic() < getattr(dialog, "_music_detect_deadline", 0.0):
        return

    timer = getattr(dialog, "_music_detect_timer", None)
    if timer is not None:
        timer.stop()
    button = dialog.music_player_detect_btn
    button.setEnabled(True)
    button.setText(_DETECT_BUTTON_LABEL)

    for key, path in results.items():
        getattr(dialog, _PICKER_ATTR[key]).setText(path)
    if results:
        log.info("音乐播放器路径自动检测：找到 %s", results)
    else:
        log.info("音乐播放器路径自动检测：未找到任何播放器")
    _report_results(dialog, results)


def _report_results(dialog, results: dict[str, str]) -> None:
    lines = []
    for key in PLAYER_KEYS:
        label = music_players.player_label(key)
        path = results.get(key)
        lines.append(f"{label}：{path}" if path else f"{label}：未找到")
    QMessageBox.information(
        dialog,
        "自动检测播放器",
        "\n".join(lines) + "\n\n没找到的可以点右侧「选择…」手动指定程序位置。",
    )
