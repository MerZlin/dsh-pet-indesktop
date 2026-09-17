# -*- coding: utf-8 -*-
"""独立设置进程（``python -m pet --settings``）的本地宿主。

设置进程刻意不导入 ``pet.app``（不载素材库/ffmpeg/托盘/灵动岛），因此进程内设置页
依赖的三项外部能力在这里用最小宿主补齐：

1. **试听本地播放**：复用 ``VoiceChimeService`` 的合成+播放通路（edge-tts 懒加载、
   音色/语速/音调/音量/缓存全部同源），只注入一个最小宿主。``voice_chime_service``
   只在用户点「立即试听」时惰性 import——standalone 启动路径不碰它。
   为什么不用 ``pet/sound_winmm.py`` 直放：edge-tts 产出 mp3，winmm 池只吃 PCM16
   wav，要走通得先引入解码/转码；QMediaPlayer 是原服务里现成的播放通路，
   复用比自建最小实现更少新代码、更少行为分叉。

2. **节日试听**：复用 ``FestivalReminderService.remind_now``（文案组装与让位逻辑
   同源），文本走宿主 ``system_notify``、语音走上面那条本地音频通道。

3. **避让桌宠位置**：没有 parent 窗口可读几何，改读配置目录里的 runtime 状态文件
   （桌宠启动/移动时写的 ``pet-runtime-v2-*.json`` / ``runtime-*.json``）。
"""

from __future__ import annotations

import importlib.util
import logging

logger = logging.getLogger(__name__)

# 惰性构造的独立进程通道子类缓存（模块顶层不 import voice_chime_service）。
_STANDALONE_CHIME_CLASS = None


def show_transient_notice(text: str) -> None:
    """独立设置进程内的可见反馈：瞬时 tooltip（非模态、不阻塞、无需点确认）。

    设置进程没有桌宠气泡可挂，模态 QMessageBox 又会卡住试听期间的界面，
    tooltip 是这里最轻的"一定看得见"通道。
    """
    from PySide6.QtGui import QCursor
    from PySide6.QtWidgets import QToolTip

    try:
        QToolTip.showText(QCursor.pos(), str(text))
    except Exception:
        logger.debug("独立设置进程提示展示失败", exc_info=True)


def _edge_tts_available() -> bool:
    """不 import edge_tts 本体地探测其可用性（与 voice_chime_service 同口径）。"""
    try:
        return importlib.util.find_spec("edge_tts") is not None
    except (ImportError, ValueError):
        return False


def _standalone_chime_class():
    """惰性取得独立进程的 VoiceChimeService 子类（首次点试听才 import 服务本体）。"""
    global _STANDALONE_CHIME_CLASS
    if _STANDALONE_CHIME_CLASS is not None:
        return _STANDALONE_CHIME_CLASS
    from .voice_chime_service import VoiceChimeService

    class _StandaloneChimeService(VoiceChimeService):
        """独立设置进程音频通道：文本反馈改走进程内瞬时提示。

        原实现的 `_bubble` 先看 ``voice_chime_show_bubble`` 再退到桌宠窗口/系统
        通知；本进程两样都没有，用户在设置页关掉气泡开关后，"edge-tts 缺失 /
        合成失败 / 播放器不可用"就会彻底静默（点了试听没反应）。这里只换文本
        通道（保留全部合成/播放/缓存逻辑），保证试听在任何配置下都有可见结果。
        """

        def _bubble(self, text: str) -> None:
            show_transient_notice(text)

    _STANDALONE_CHIME_CLASS = _StandaloneChimeService
    return _STANDALONE_CHIME_CLASS


class _StandaloneHost:
    """VoiceChimeService / FestivalReminderService 的最小宿主。

    两个服务只依赖 ``config`` / ``win`` / ``system_notify`` /
    ``ensure_audio_channel`` 这组鸭子面（见 voice_chime_service._bubble 与
    festival_service._speak），不需要真正的 AppShell。
    """

    def __init__(self, config, parent=None) -> None:
        self.config = config
        # 独立进程没有桌宠窗口：win 保持 None，气泡路径自然落到 system_notify。
        self.win = None
        self._parent = parent
        self._chime = None
        self._festival = None

    def system_notify(self, title: str, message: str, **_kwargs) -> None:
        show_transient_notice(message)

    def ensure_audio_channel(self):
        """节日语音复用报时服务的音频通道（与进程内同一条通路）。"""
        return self.chime_service()

    def chime_service(self):
        if self._chime is None:
            self._chime = _standalone_chime_class()(self)
        return self._chime

    def festival_service(self):
        if self._festival is None:
            from .festival_service import FestivalReminderService

            self._festival = FestivalReminderService(self)
        return self._festival

    def shutdown(self) -> None:
        """设置窗关闭/进程退出：停掉在跑的音频通道（飞行中的合成结果一并作废）。"""
        for service in (self._chime, self._festival):
            if service is None:
                continue
            try:
                service.stop()
            except Exception:
                logger.debug("独立设置进程停止服务失败", exc_info=True)


def _guard_voice_preview(dialog) -> None:
    """edge-tts 缺失时直接禁用试听按钮并说明——比"点了没声"更清楚。"""
    if _edge_tts_available():
        return
    page = getattr(dialog, "voice_chime_page", None)
    button = getattr(page, "preview_btn", None)
    if button is None:
        return
    button.setEnabled(False)
    button.setToolTip("独立设置进程试听需要 edge-tts 库：请运行 pip install edge-tts 后重试")


def preview_voice_chime(dialog, text: str = "") -> None:
    """语音报时设置页「立即试听」：走本地音频通道（不依赖主进程）。"""
    host = getattr(dialog, "_standalone_host", None)
    if host is None:
        return
    host.chime_service().say_now(text)


def preview_festival(dialog) -> None:
    """节日设置页「立即试听」：本地演示今日节日（文案+可选语音）。"""
    host = getattr(dialog, "_standalone_host", None)
    if host is None:
        return
    host.festival_service().remind_now()


def standalone_pet_geometry(config):
    """读配置目录 runtime 状态文件，返回一只活桌宠窗口的几何矩形。

    设置进程没有 parent 窗口可读几何，只能取桌宠自己留下的运行时标记。
    ``slot_manager.read_live_instances`` 同时认 ``pet-runtime-v2-*.json``（多窗）
    与旧 ``runtime-*.json``，并顺手清掉死进程/损坏的标记。读不到返回 None，
    调用方保持默认位置（不许因为读文件失败而崩）。
    """
    from PySide6.QtCore import QRect

    from . import slot_manager as slot_manager_mod

    try:
        instances = slot_manager_mod.read_live_instances(config.dir)
    except Exception:
        logger.debug("读取桌宠运行时标记失败", exc_info=True)
        return None
    for _pid, x, y, w, h in instances:
        if w > 0 and h > 0:
            return QRect(int(x), int(y), int(w), int(h))
    return None


def install_standalone_hooks(dialog) -> None:
    """给独立进程的设置对话框接上本地宿主（幂等）。

    只挂独立进程需要的东西，进程内（standalone=False）实例完全不走这里。
    """
    if getattr(dialog, "_standalone_host", None) is not None:
        return
    host = _StandaloneHost(dialog.config, parent=dialog)
    dialog._standalone_host = host  # 强引用保活：服务/播放器都挂在宿主上
    # 节日试听：FestivalSettingsPage 沿 parent 链向上找 on_festival_now（进程内由
    # PetWindow 提供）。独立进程只在对话框这一层挂同名属性；进程内实例不设该属性，
    # 因此查找仍会穿过对话框落到 PetWindow，行为逐位不变。
    dialog.on_festival_now = lambda: preview_festival(dialog)
    _guard_voice_preview(dialog)
    dialog.finished.connect(lambda _result: host.shutdown())
