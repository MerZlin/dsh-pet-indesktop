# -*- coding: utf-8 -*-
"""
应用入口 —— QApplication + 桌宠窗口 + 系统托盘。

支持运行时切换角色：
- 右键桌宠 →「切换角色」
- 托盘菜单 →「切换角色」
切换后会热加载对应形象的 webm，并保留位置/朝向等配置。

批5.1（纯重构）把原 PetApp 按「进程级 / 每窗」拆成两块，行为逐位不变；
批5.2 spike 扩成多窗集合（AppShell 持有 ``_instances`` 列表，``self.instance``
指主窗 = 列表头，兼容既有调用面）：
- ``AppShell``：进程级服务（托盘、灵动岛、dock 菜单、更新、余额、系统通知、
  broker、aboutToQuit 收口），持有 ``PetInstance`` 集合。
- ``PetInstance``：每窗容器（config/lib/win/聊天窗/设置窗/气泡），持 backref
  到 ``AppShell``，窗口级操作都从这里路由，``self.win`` 主窗单窗假设据此收敛。
"""

from __future__ import annotations

import json
import logging
import os
import queue
import sys
import threading
import time
import weakref
from logging.handlers import RotatingFileHandler
from pathlib import Path

import shiboken6
from PySide6.QtCore import QObject, QPoint, QRect, QTimer, Qt, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QSystemTrayIcon, QWidget

from .platform_mac import _mac_set_dock_icon_visible

from . import autostart as autostart_mod
from . import catalog
from . import click_sound
from . import feature_activation as feature_activation_mod
from . import overlay_instance_gate as overlay_gate_mod
from .voice import self_talk as self_talk_voice
from . import slot_manager as slot_manager_mod
from . import updater
from . import webm_clip as webm_clip_mod
from .config import APP_DIR_NAME, Config, _default_base
from .context_menus.icons import vector_menu_icon
from .context_menus.shared import add_tray_action, open_deepseek_web
from .desktop_notify import DesktopNotification, position_stack
from .harness_launcher import launch_harness_gui
from .library import MovieLibrary
from .window import PetWindow
from .fun_image_popup import restore_ojingjing_windows
from .runtime_cleanup import cleanup_stale_runtime_dirs
from .session_watcher import install_session_watcher
from .decode_fanout import DecodeFanoutHub
from .todo.reminder import TodoReminderService
from .voice.chime_service import VoiceChimeService
from .persona_phrases import PhrasePicker


_persona_pickers = weakref.WeakKeyDictionary()

# 存活 AppShell 注册表（测试收口用，与 agent_link._LIVE_AGENT_LINK_MANAGERS
# 同一纪律）。多窗共享子系统与待办服务
# 持有无主 QTimer（`QTimer()` + timeout.connect），其连接从 Qt C++ 侧强引用
# 住整个 shell 对象图，Python 的 gc.collect() 回收不掉；解释器退出时的 GC
# 才最终化这些 Qt 对象 → 原生访问违规（Windows 0xC0000005，崩溃点落在
# "Garbage-collecting / <no Python frame>"）。WeakSet 只弱引用 shell 本身，
# 测试收口时逐对象停表并释放反向引用。
_LIVE_SHELLS: "weakref.WeakSet" = weakref.WeakSet()


class _BackgroundResult(QObject):
    done = Signal(bool, object)


def _presentation_available(host) -> bool:
    """这个宿主现在还能不能承载一次**呈现**（迟到的服务结果用）。

    ``_SpriteServicePresenter``（overlay 菜单的手动余额/天气落点）自带"发起的那一只
    还在吗"的判据；普通宿主（PetWindow / 壳 / 测试替身）没有这一面，一律按可用——
    它们本来就是"这一份就是唯一落点"的形态。判据是对象身份（同一槽重建出来的是另一只
    宠），比槽号 + 代次更强：旧结果绝不投给新住客（规范 §一「关闭后的迟到结果」）。
    """
    check = getattr(host, "presentation_available", None)
    if not callable(check):
        return True
    try:
        return bool(check())
    except RuntimeError:
        # 已销毁的原生窗口/壳：按不可用处理（不冒险触碰）
        return False


class _BalanceBridge(_BackgroundResult):
    def __init__(self, win, owner=None):
        super().__init__()
        self.win = win
        self.owner = owner
        self.done.connect(self._show)

    def _show(self, ok: bool, payload) -> None:
        # 异步回调可能晚于窗口销毁（切角色/退出），先探活再触碰 Qt 对象
        if self.win is None or not shiboken6.isValid(self.win):
            return
        if not _presentation_available(self.win):
            # 发起的那一只已经不在了（或换成了另一只）：**只取消这次呈现**。
            # 数据侧不受影响——内存/文件缓存由 worker 落好，岛卡片是全局面，
            # 用户问的余额信息仍然到位（规范 §一「只取消宠物呈现」）。
            logging.info("余额结果到达时发起桌宠已不在，取消本次呈现（缓存仍更新）")
            if ok and self.owner is not None and hasattr(self.owner, "_update_island_balance"):
                self.owner._update_island_balance(payload)
            return
        if not ok:
            self.win.show_bubble(str(payload), duration_ms=6000)
            return
        _show_balance_payload(self.win, payload)
        if self.owner is not None and hasattr(self.owner, "_update_island_balance"):
            self.owner._update_island_balance(payload)


class _QuietBalanceBridge(_BackgroundResult):
    """灵动岛卡片展开时的静默余额查询：只更新岛卡片，不冒泡、不播余额动画。"""

    def __init__(self, owner):
        super().__init__()
        self.owner = owner
        self.done.connect(self._apply)

    def _apply(self, ok: bool, payload) -> None:
        owner = self.owner
        island = getattr(owner, "island", None) if owner is not None else None
        if island is None or not shiboken6.isValid(island):
            return
        if ok:
            owner._update_island_balance(payload, animate=False)
        else:
            island.set_balance_info(owner._island_tier_hint(), "余额查询失败")


def _persona_picker(win):
    """Return an application-owned picker without extending PetWindow state."""
    try:
        picker = _persona_pickers.get(win)
    except (TypeError, RuntimeError):
        picker = None
    if picker is None:
        picker = PhrasePicker()
        try:
            _persona_pickers[win] = picker
        except (TypeError, RuntimeError):
            pass
    return picker


def _persona_text(win, key: str, fallback: str, **values) -> str:
    """Render a configured persona phrase for application-level messages."""
    cfg = getattr(win, "cfg", None)
    if cfg is None:
        return fallback.format(**values)
    mode = str(cfg.get("dialogue_mode", "legacy") or "legacy")
    picker = _persona_picker(win)
    if mode == "custom":
        text = picker.custom(cfg.get("dialogue_phrases", {}), key, fallback, **values)
        # 与内置模式同语义：未命中自定义文案时回退并填充占位符（含 {text} 等）。
        # 此前直接 return 会把未格式化的 fallback 露出字面量 {…}。
        return fallback.format(**values) if text is fallback else text
    # legacy / whale_maid：命中内置 JSON 预设即渲染，未命中回退调用方原文案
    text = picker.get(mode, key, fallback, **values)
    return fallback.format(**values) if text is fallback else text


def _show_balance_payload(win, payload) -> None:
    """展示余额气泡（含峰谷副标题）并按余额档位触发余额动画。

    网络查询、内存缓存、文件缓存三条路径统一走这里，避免缓存命中时
    没有副标题/不播动画导致的行为不一致。

    pet.island.balance 按需导入（内存瘦身第二刀）：该模块自带整套档位文案/价格提示
    表，实测独占 4.75MB Python 堆（tools/import_cost.py），而余额查询在
    默认配置下从不发生（balance_refresh_minutes=0 且用户没点"看余额"）——
    启动就不该为它付这份常驻。函数内 import 只在真正要显示余额时才付。
    """
    from .island import balance as balance_mod

    if win is None or not shiboken6.isValid(win):
        return
    if isinstance(payload, dict):
        text = str(payload.get("text") or "余额信息为空")
        info = payload.get("info") or {}
    else:
        text = str(payload)
        info = {}
    cfg = getattr(win, "cfg", None)
    if cfg is not None:
        text = _persona_text(win, "balance.result", "余额情况：{text}", text=text)
    cfg = getattr(win, "cfg", None)
    mode = str(cfg.get("balance_tier_labels_mode", "default") or "default") if cfg is not None else "default"
    custom_peak = str(cfg.get("balance_tier_label_peak", "") or "") if cfg is not None else ""
    custom_idle = str(cfg.get("balance_tier_label_idle", "") or "") if cfg is not None else ""
    peak_label, idle_label = balance_mod.resolve_tier_labels(mode, custom_peak, custom_idle)
    color_enabled = bool(cfg.get("balance_tier_color_enabled", True)) if cfg is not None else True
    if color_enabled:
        subtitle = balance_mod.deepseek_pricing_hint_html(
            peak_label=peak_label, idle_label=idle_label,
        )
    else:
        subtitle = balance_mod.deepseek_pricing_hint(
            peak_label=peak_label, idle_label=idle_label,
        )
    # 只保护这一次上屏的字符串。缓存、format_balance 和接口原文仍是明文。
    text = balance_mod.format_balance_display(text)
    win.show_bubble(
        text, duration_ms=6000,
        subtitle=subtitle,
    )
    # 按余额档位播放上游余额动画（仅当素材存在时静默跳过）
    p = balance_mod.balance_percent(info.get("total"))
    if p is not None:
        idx = balance_mod.balance_event_index(p)
        name = balance_mod.BALANCE_EVENT_NAMES[idx]
        if name and hasattr(win, "request_link_anim"):
            win.request_link_anim(name)


def _instance_config_identity(instance) -> str:
    """实例的配置身份（"" = 主身份，``slot-N`` = 子宠；slot_manager 同口径）。

    独立设置进程按这个身份选 ``config.json`` / ``config-slot-N.json``，进程内多窗
    下也是"这个设置页属于哪只宠"的判据（``_launch_settings_process`` 与设置页占用
    提示共用一处实现）。
    """
    return str(getattr(getattr(instance, "config", None), "instance_id", "") or "")


class _OverlayChatHost:
    """完整聊天窗的 pet 形宿主：overlay 拓扑没有 PetWindow，用壳拼出它真正读到的面。

    只做转发，不新增机制（``overlay_shell`` 的 ``visible_content_rect`` /
    ``icon_pixmap`` 已是 PetWindow 同契约的 sprite 等价物）：

    - 定位 ``visible_content_rect``（``position_near_pet`` 的正路）；
    - ``frameGeometry`` 兜底（身体框为空时才被读，拿合成窗几何顶替，避免
      AttributeError）；
    - 头像 ``icon_pixmap``（聊天窗标题栏取角色图）；
    - ``add/remove_position_listener``（``chat_follow_pet`` 勾选时聊天窗跟随桌宠，
      转发到合成窗的 sprite 位置监听）。
    """

    __slots__ = ("_shell",)

    def __init__(self, shell) -> None:
        self._shell = shell

    def visible_content_rect(self) -> QRect:
        fn = getattr(self._shell, "visible_content_rect", None)
        return fn() if callable(fn) else QRect()

    def frameGeometry(self) -> QRect:  # noqa: N802 (Qt 命名)
        overlay = getattr(self._shell, "overlay", None)
        return overlay.geometry() if overlay is not None else QRect()

    def icon_pixmap(self, size: int = 64):
        fn = getattr(self._shell, "icon_pixmap", None)
        return fn(size) if callable(fn) else None

    def add_position_listener(self, listener) -> None:
        overlay = getattr(self._shell, "overlay", None)
        sprite = getattr(self._shell, "sprite", None)
        if overlay is not None and sprite is not None and callable(listener):
            overlay.add_position_listener(sprite, listener)

    def remove_position_listener(self, listener) -> None:
        overlay = getattr(self._shell, "overlay", None)
        sprite = getattr(self._shell, "sprite", None)
        if overlay is not None and sprite is not None:
            overlay.remove_position_listener(sprite, listener)


class _EmptyPetHost:
    def __init__(self, shell):
        self.cfg = shell.config
        self.scale = 1.0
        self._speech_bubble = None
        self._bubble_suppressed = False
        self._alert_current = None
        self._sticky_bubble_active = False
        self._physics_mode = None
        self._dragging = False

    def isVisible(self):  # noqa: N802
        return False

    def visible_content_rect(self):
        return QRect()

    def show_bubble(self, *args, **kwargs):
        return False

    def show_alert(self, *args, **kwargs):
        return False

    def resolve_alert(self, *args, **kwargs):
        return None

    def hold_bubble(self, *args):
        return None

    def set_bubble_suppressed(self, suppressed):
        self._bubble_suppressed = bool(suppressed)


class _UpdateBridge(_BackgroundResult):
    def __init__(self, parent, owner=None):
        super().__init__()
        self.parent = parent
        self.owner = owner
        self.done.connect(self._show)

    def _show(self, ok: bool, payload) -> None:
        # 异步回调可能晚于窗口销毁，先探活再触碰 Qt 对象
        alive = self.parent is not None and shiboken6.isValid(self.parent)
        if not ok:
            if alive:
                self.parent.show_bubble(f"检查更新失败：{payload}", duration_ms=7000)
            return
        release = payload
        tag = str(release.get("version", ""))
        if not updater.is_newer(tag):
            if alive:
                self.parent.show_bubble(f"已经是最新版本（{updater.APP_VERSION}）啦")
            return
        if alive:
            self.parent.show_bubble(
                f"发现新版本 v{tag}（当前 {updater.APP_VERSION}）。"
                "正在打开“更新”页，可直接下载并安装。",
                duration_ms=9000,
            )
            owner = self.owner
            instance = getattr(owner, "instance", None) if owner is not None else None
            open_settings = getattr(instance, "open_modern_settings", None)
            if callable(open_settings):
                QTimer.singleShot(0, lambda: open_settings(initial_page="更新"))


# 批5.2 §③.7：多窗日志用 [slot-N] 前缀区分。单进程多窗共享一份日志文件，
# 故用线程本地记录「当前执行的窗」由日志 Filter 加前缀；flag 关（单进程单窗）
# 时前缀为 None，日志格式与现状逐位一致。
_pet_log_slot = threading.local()


class _SlotLogFilter(logging.Filter):
    """按线程本地槽位给日志记录加 [slot-N] 前缀（flag 开/多窗时）。"""

    def filter(self, record: logging.LogRecord) -> bool:
        slot = getattr(_pet_log_slot, "slot", None)
        if slot:
            record.msg = f"[{slot}] {record.msg}"
        return True


def _setup_logging(config: Config) -> None:
    config.dir.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        str(config.dir / f'pet-{os.getpid()}.log'),  # 多开实例日志按 PID 隔离，避免互相覆盖
        maxBytes=1_000_000, backupCount=2, encoding='utf-8',
    )  # 滚动日志：1MB×2，不再无限增长
    handler.addFilter(_SlotLogFilter())
    logging.basicConfig(
        handlers=[handler],
        level=logging.INFO,
        format='%(asctime)s %(levelname)s %(message)s',
        encoding='utf-8',
    )
    _cleanup_old_pet_logs(config.dir)


def _cleanup_old_pet_logs(log_dir, *, max_age_days: float = 7.0) -> int:
    """启动时清理过期的 pet-<pid>.log（含滚动备份 .log.1/.2）。

    每实例每次启动都产生新文件，不清理会无界累积（审查 GLM-M2）。
    只删本变体命名空间下超龄文件；失败静默（清理不影响启动）。
    """
    removed = 0
    try:
        cutoff = time.time() - max_age_days * 86400
        for path in Path(log_dir).glob('pet-*.log*'):
            try:
                if path.is_file() and path.stat().st_mtime < cutoff:
                    path.unlink()
                    removed += 1
            except OSError:
                pass
    except OSError:
        pass
    return removed


def _show_startup_error(title: str, message: str) -> None:
    QMessageBox.critical(None, title, message)


def _cleanup_stale_runtime_dirs() -> None:
    """清理 PyInstaller onefile 遗留的 ``_MEI*`` 临时目录。

    只扫描系统临时目录中超过 24 小时的目录，并始终跳过当前进程的
    ``sys._MEIPASS``。删除失败只记录日志，不接管 ACL，也不影响启动。
    """
    if not getattr(sys, "frozen", False):
        return
    meipass = getattr(sys, "_MEIPASS", None)
    if not meipass:
        return

    current = Path(meipass).resolve(strict=False)
    result = cleanup_stale_runtime_dirs(current_dir=current)
    for directory in result.removed:
        logging.info("已清理遗留 PyInstaller 缓存目录: %s", directory)
    for directory, error in result.failed.items():
        logging.warning("清理 PyInstaller 缓存目录失败: %s (%s)", directory, error)


def _read_spawn_offset_env() -> int:
    """P0-1：读取 spawn 子进程路径写入的 DSH_PET_SPAWN_OFFSET_INDEX，显式传给
    主窗 PetInstance.spawn_offset（进程 spawn 路径依赖它错开落位）。

    该 env 是历史多进程 spawn 的落点错峰通道，4.4b 后不再有进程 spawn，但
    **兼容解析保留**（T5）：存量快捷方式/外壳脚本仍可能带它，读到就照旧接到
    主窗的 spawn_offset 构造参数。
    """
    try:
        return max(0, int(os.environ.get('DSH_PET_SPAWN_OFFSET_INDEX', '0') or '0'))
    except (TypeError, ValueError):
        return 0


# 播放器路径预热的错峰延迟：排在动画预热之后，避开多开启动期的 IO 洪峰。
_MUSIC_PLAYER_WARM_DELAY_MS = 2500

#: 归「手动视觉 vision」模块的全局快捷键动作（注册与回调双守的共用清单）。
#: ``hotkey_open_chat`` 不在此列：chat 的闸在 enable_chat 单源上。
_VISION_HOTKEY_ACTIONS = frozenset({"look_screen", "screen_capture"})


def _warm_music_player_paths() -> None:
    """后台线程暖音乐播放器路径缓存（幂等，可重复调用）。

    右键菜单「打开某播放器给主人放歌」的 builder 在 GUI 线程只读缓存（冷缓存
    时乐观启用）：真正的目录浅扫在这里的 daemon 线程里做，否则冷缓存下首次开
    菜单要在 MainThread 上扫 6s+。

    **启动只预热下面这两键**（``netease`` / ``qqmusic``）：注册表后补的酷狗、
    汽水不在启动预热点里，它们的缓存由首次构建音乐子菜单时的幂等预热（或设置页
    「自动检测」）补齐——菜单条目不因此置灰（冷缓存一样乐观启用），只是那一次
    扫描发生在打开菜单之后。四条串行浅扫在本机要二十多秒，压在启动路径上会拖慢
    首屏。这条口径由 ``tests/test_music_player_cache.py`` 的
    ``test_ui_ready_prewarm_schedules_music_player_cache_warm`` 钉住。
    """
    from .music import players as music_players

    for player_key in ("netease", "qqmusic"):
        music_players.warm_cache_async(player_key)


def _schedule_music_player_warm() -> None:
    """UI 就绪后统一预热点的一环：延迟几秒再后台暖播放器路径缓存。

    用单次 QTimer 错峰，**不阻塞任何线程**（这里只是登记一个延迟任务）。

    音乐模块未激活：连延迟任务都不登记——播放器路径只服务「打开播放器」与
    「自动检测」，模块停用后这条浅扫链（启动期两条 daemon 线程 + 全局串行
    锁）没有任何消费者。
    """
    if not feature_activation_mod.is_active("music"):
        return
    QTimer.singleShot(_MUSIC_PLAYER_WARM_DELAY_MS, _warm_music_player_paths)


class PetInstance:
    """每窗容器 —— config/lib/win/聊天窗/设置窗/气泡与全部窗口级操作。

    批5.1（纯重构）拆自原 PetApp 的「每窗」半边，行为逐位不变，运行时仍
    一进程一窗。持 backref 到所属 ``AppShell``；窗口级逻辑集中于本类后，
    ``self.win`` 单窗假设只有一个归属点，便于批5.2 扩成多窗集合。
    """

    def __init__(self, shell: "AppShell", config: Config, enable_chat: bool = True,
                 slot_id: int | None = None,
                 spawn_offset: int = 0) -> None:
        self.shell: AppShell = shell
        self.config = config
        self.slot_id = slot_id
        self._spawn_offset = max(0, int(spawn_offset if spawn_offset is not None else 0))
        self.win: PetWindow | None = None
        self.chat_window = None
        self.legacy_chat_window = None
        self.modern_chat_window = None
        self.chat_settings_dialog = None
        self.modern_settings_dialog = None
        self.quick_chat = None
        self._pending_dialog_opens: set[str] = set()
        # E2（REVIEW_batch51）：enable_chat 单源在 AppShell（进程级），本类只读转发。
        self.enable_chat = bool(enable_chat)
        # 4.4a 停用多进程多宠退役层：本窗不再自持碰撞 IPC 会话——多宠碰撞由
        # overlay 拓扑的 SpriteCollisionWorld（进程内统一世界）承载；legacy
        # 拓扑退化为单宠/进程内多窗渲染面，不再跨进程交换冲量。
        # 批5.3：进程级共享解码 hub（AppShell 持有，各窗共用同一份）。此前
        # broker 是每窗一个 shm facade；现换成进程级 DecodeFanoutHub（fan-out
        # 与碰撞角色解耦，不骑 QLocal），窗口调用点/参数名零改。
        self.broker_facade = getattr(self.shell, '_decode_hub', None)

    @property
    def enable_chat(self) -> bool:
        """单源：转发到 AppShell（进程级），批5.2 消除双源漂移（REVIEW_batch51 E2）。

        无 shell（__new__ 测试桩/最小替代）时回退到本地 _enable_chat 兜底。
        """
        shell = getattr(self, "shell", None)
        if shell is not None:
            return shell.enable_chat
        return bool(getattr(self, "_enable_chat", True))

    @enable_chat.setter
    def enable_chat(self, value):
        # P2-4：setter 仅缓存构造值作无 shell 兜底，**从不改写进程级 shell**
        #（只读转发——读取走 getter 的 shell.enable_chat 权威源）。
        self._enable_chat = bool(value)

    @property
    def chat_build_capable(self) -> bool:
        """构建变体能力（不含模块闸）：转发 shell，兜底回本地构造值。"""
        shell = getattr(self, "shell", None)
        if shell is not None:
            return shell.chat_build_capable
        return bool(getattr(self, "_enable_chat", True))

    # ------------------------------------------------------------ 窗口构建
    def _create_library(self, character_id: str) -> MovieLibrary:
        # 预热策略：默认 balanced（瞬时交互核 pinned 预热首帧，随机动作池
        # 按需解码）。预热开关已并入省电模式：省电开启 = 闲置降帧 + 关闭
        # 后台预热（此处经 prewarm_enabled 传入，设置保存后由
        # _sync_animation_prewarm 同步）。media_prewarm 键保留给高级用户。
        prewarm = str(self.config.get("media_prewarm", "balanced") or "balanced")
        # 首帧缓存全局预算（高级用户可在 config.json 调小，省电/低配机用）；
        # 进程级设置，幂等，切角色重复调用无害。
        webm_clip_mod.set_first_frame_budget(
            int(self.config.get("first_frame_cache_max_mb", 8)) * 1024 * 1024
        )
        lib = MovieLibrary(
            character_id=character_id,
            prewarm_policy=prewarm,
            prewarm_enabled=not bool(self.config.get("idle_low_fps_enabled", False)),
        )
        # UI 就绪后统一调度预热：高优先级立即后台跑（带 0~0.05s 错峰），
        # 随机动作池延迟 2s 补全，避免多开启动时 ffmpeg 进程洪峰。
        lib.schedule_high_priority_warm()
        lib.schedule_low_priority_warm()
        # 首跑帧序列自动供给（B 档）：延迟 5s 后台低优转热集，静默 no-op 兜底
        getattr(lib, 'maybe_provision_frameseq', lambda: None)()
        # 同批低优先级预热：播放器路径缓存（右键菜单只读缓存，冷缓存时后台先扫）。
        _schedule_music_player_warm()
        logging.info('素材加载完成：%s %d 段动画', character_id, len(lib.names()))
        return lib

    def _slot_wrap(self, fn):
        """包一层：多窗时把线程本地日志槽位设为本窗 slot（批5.2 §③.7）。

        4.4b：进程内多窗常开化后不再有 flag 快照——按**活窗数**判定：单窗
        （含 legacy 默认形态）槽位为 None，日志格式与历史逐位一致；≥2 窗时
        槽位为 'slot-N'，由 _SlotLogFilter 加 [slot-N] 前缀。
        """
        if fn is None:
            return None
        slot = f"slot-{self.slot_id}" if self.slot_id is not None else "slot-0"

        def wrapper(*args, **kwargs):
            prev = getattr(_pet_log_slot, "slot", None)
            if len(getattr(self.shell, "_instances", ())) > 1:
                _pet_log_slot.slot = slot
            else:
                _pet_log_slot.slot = None
            try:
                return fn(*args, **kwargs)
            finally:
                _pet_log_slot.slot = prev

        wrapper.__name__ = getattr(fn, "__name__", "wrapper")
        wrapper.__doc__ = getattr(fn, "__doc__", None)
        return wrapper

    def _wire_window(self, win: PetWindow) -> None:
        """绑定新窗口的回调接线（创建与角色切换共用，两处历史逐行重复）。

        两段原始代码逐行一致（并集 = 该段本身，未发现任一方多设回调），
        后续新增回调只改这一处即可保证两个入口同步。
        进程级操作（余额/更新/生小肥鱼/系统通知）经 ``self.shell`` 路由，
        其余均为本窗操作。批5.2 用 ``_slot_wrap`` 给每窗回调加日志槽位。
        """
        win.on_switch_character = self._slot_wrap(self.switch_character)
        # 构建变体能力（不含 chat 模块闸）：harness 菜单可用性按它判（registry）
        win.chat_build_capable = self.chat_build_capable
        win.on_open_chat = self._slot_wrap(self.open_chat) if self.enable_chat else None
        win.on_open_quick_chat = self._slot_wrap(self.open_quick_chat) if self.enable_chat else None
        win.on_open_chat_settings = self._slot_wrap(self.open_chat_settings) if self.enable_chat else None
        win.on_show_balance = self._slot_wrap(self.shell.show_balance) if self.enable_chat else None
        # 天气：进程级服务（AppShell.show_weather），与聊天能力无关（独立外部数据源）
        win.on_show_weather = self._slot_wrap(self.shell.show_weather)
        win.on_check_update = self._slot_wrap(self.shell.check_update)
        win.on_look_synced = self._slot_wrap(self.sync_look_to_chat) if self.enable_chat else None
        win.on_look_screen = win.look_at_screen if self.enable_chat and hasattr(win, "look_at_screen") else None
        # 截图（快速识屏 P0）：状态与短结果上灵动岛——岛归 AppShell 所有，窗口侧
        # 没有别的路径，这里显式注入取岛回调（岛会被重建，所以传可调用者而非实例）。
        win.screen_capture_island = lambda: getattr(self.shell, "island", None)
        win.on_open_modern_settings = self._slot_wrap(self.open_modern_settings)
        # 桌宠隐藏时的气泡改道面（DSH 联动等非交互反馈气泡 → 灵动岛，见
        # window_alerts.redirect_hidden_bubble）；岛对话不可用时注入方返回 False。
        win.hidden_bubble_redirect = self.shell._island_feedback_bubble
        # 反馈面可用性探针：隐藏期联动监视器是否跳过低功耗暂停（mixin 消费）。
        win.island_feedback_available = self.shell._island_feedback_available
        win.on_spawn_pet = self._slot_wrap(self.shell.spawn_pet)
        # 「退出子肥鱼」只挂给主肥鱼（instance_id 为空）：子肥鱼进程里该入口的
        # pid==os.getpid() 自我保护会跳过子鱼自己、把主鱼当子鱼 taskkill 掉
        #（实机事故：从子鱼触发清除 → 主鱼被杀、触发的那只子鱼存活）。
        win.on_clear_spawned_pets = (
            self._slot_wrap(self.shell.clear_spawned_pets)
            if self.slot_id in (None, 0) else None)
        # 「恢复默认设置」后的重置级运行态对齐：进程内设置页直接调这里（独立设置
        # 进程没有窗可依附，走 D12 指令通道 → AppShell._consume_settings_command）。
        win.on_settings_reset = self._slot_wrap(
            lambda: self.shell.apply_settings_reset(self))
        win.on_open_todo_panel = self._slot_wrap(self.shell.open_todo_panel)
        # 网络信息（issue #172）：右键菜单入口 → 进程级弹窗（overlay 侧由
        # SpriteMenuFacade.on_network_info 走同一方法）。
        win.on_network_info = self._slot_wrap(self.shell.open_network_info)
        win.on_voice_chime_now = self._slot_wrap(self.shell.trigger_voice_chime_now)
        win.on_toggle_voice_chime = self._slot_wrap(self.shell.toggle_voice_chime)
        # 点击自言自语的朗读走同一条音频通道（AppShell 持有，进程内唯一）。
        win.on_self_talk_speak = self._slot_wrap(self.shell.speak_self_talk)
        win.on_festival_now = self._slot_wrap(self.shell.trigger_festival_now)
        win.on_toggle_festival = self._slot_wrap(self.shell.toggle_festival_reminder)
        # 每日一签：菜单「抽支签」→ 出当天的签；on_user_interaction 供
        # 窗口在"用户点了桌宠"时回调（当天没看才提示一行）。
        win.on_divination_draw = self._slot_wrap(self.shell.trigger_divination_draw)
        win.on_user_interaction = self._slot_wrap(self.shell.divination_hint_on_interaction)
        win.on_restore_fun_windows = restore_ojingjing_windows
        win.on_hidden = self._slot_wrap(self._notify_pet_hidden)
        # 4.4b：窗级「退出这只」恒注入（多窗常开化；最后一窗退出即全进程退出，
        # 与旧 flag 关时的 app.quit 分支语义等价）。
        win.on_exit_window = self._slot_wrap(self._request_exit_window)

    def _build_window(self, character_id: str, lib: MovieLibrary | None = None,
                      build_tray: bool = True) -> PetWindow:
        """创建新窗口并完成接线、音效预热与旧对象延迟销毁（创建与切换共用）。

        从 _create_ui 与 switch_character 两处历史逐行重复的公共序列（约 25 行）
        抽出：步骤顺序与 deleteLater / QTimer.singleShot 时序与原实现完全一致。
        lib 可预传入（switch_character 先预创建、失败则保留当前角色），
        缺省时按 character_id 创建（_create_ui 启动路径）。
        托盘为进程级（AppShell 持有），经 ``self.shell`` 路由。
        build_tray=False 供批5.2 进程内多窗使用：非主窗不再新建/替换托盘
        （复用 `shell._refresh_tray_menu` 聚合各窗菜单）。
        """
        if lib is None:
            lib = self._create_library(character_id)
        # 4.4b：进程级共享子系统（agent_link / proactive / 全屏 watcher）常建，
        # 各窗引用同一份；窗口全屏监视交由共享 watcher（本窗不再自建线程）。
        shared = getattr(self.shell, "_shared", None)
        # 批5.2a §③.5：**子窗**构造期日志加 [slot-N] 前缀（主窗 slot 0 保持
        # 历史格式不变）；运行时动画/物理等 GUI 线程日志不动 window.py。
        _prev_slot = getattr(_pet_log_slot, "slot", None)
        if self.slot_id:
            _pet_log_slot.slot = f"slot-{self.slot_id}"
        try:
            win = PetWindow(lib, self.config,
                            broker_facade=self.broker_facade,
                            agent_link_manager=shared.agent_link if shared else None,
                            proactive_watcher=shared.proactive if shared else None)
        finally:
            _pet_log_slot.slot = _prev_slot
        token = getattr(self.config, "profile_generation", None)
        win.set_profile_identity(character_id, token() if callable(token) else None)
        win.shared_fullscreen_watcher_active = shared is not None
        self._wire_window(win)
        # 文件投喂（拖文件模拟吃掉）：PR73 引入的接线在批5.2 重构时被丢，
        # 必须随每只窗的创建（启动/切角色/多窗）挂载，缺失则拖放无效。
        win.install_file_eater()
        # 拖文件解读（确认 → 新会话请求 → 进度冒泡 → 摘要），与投喂共用接缝。
        win.install_file_interpreter()
        # 预热点击音效：首次创建 QSoundEffect/QMediaPlayer 池并等待加载完成，
        # 在显示窗口前完成，避免窗口出现后主线程被音频初始化阻塞、
        # 首次点击 Q 弹卡顿。音效关闭时不预热，避免无谓拉起 QtMultimedia 池。
        if self.config.get("click_sound_enabled", True) or self.config.get("collision_sound_enabled", True):
            click_sound.warm_click_sound_effects(
                self.config.get("click_sound_pack"),
                data_dir=self.config.dir,
            )
        win.show()

        tray = self.shell._build_tray(win) if build_tray else None

        # 清理旧对象（热切换时使用）
        old_win = self.win
        old_tray = self.shell.tray
        self.win = win
        if build_tray:
            self.shell.tray = tray
        # 批5.2a（复审 P1-1）：接入共享联动链必须在 self.win = win 之后——
        # 扇出按 instances[].win 动态遍历，早了会让新窗永远拿不到 provider。
        if shared is not None:
            self.shell._wire_shared_subsystems()

        if old_win is not None:
            old_win.hide(notify=False)
            QTimer.singleShot(0, old_win.deleteLater)
            # 仅主窗（build_tray=True）换托盘；非主窗绝不 hide/deleteLater 共享托盘。

            if build_tray and old_tray is not None:
                old_tray.hide()
                QTimer.singleShot(0, old_tray.deleteLater)
        return win

    def _create_ui(self, character_id: str) -> None:
        self._build_window(character_id)

    # ------------------------------------------------------------ 角色切换
    def switch_character(self, character_id: str) -> None:
        if self.win is None:
            return
        current = str(self.config.get('character', catalog.DEFAULT_CHARACTER))
        if character_id == current:
            return

        # 先保存配置，即使后续加载失败也记住用户选择
        self.config.set('character', character_id)
        self.config.save()

        try:
            # 预创建新库，失败则保留当前角色（在动旧窗口之前完成）
            lib = self._create_library(character_id)
        except Exception as exc:
            logging.exception('切换角色失败: %s', character_id)
            _show_startup_error('切换角色失败', str(exc))
            return

        logging.info('切换角色: %s -> %s', current, character_id)

        # 批5.2 P1-1：碰撞会话由**本窗**自持（每窗一个）——热切换
        # 只重建本窗的 session（detach 旧窗 client → 停/重建本窗会话 →
        # 新窗 attach 到新会话）。C2 前向地雷（多窗下任一窗热切换拆共享进程级
        # IPC）随「不再共享」自然消解。
        # 4.4a：碰撞 IPC 会话随多进程多宠退役层停用；热切换只做解码侧收尾。
        # 批5.3 起共享解码为进程级 hub（DecodeFanoutHub），不随热切换停；
        # 窗侧只经 _broker_unregister 逐素材收尾。
        old_win = self.win
        old_win.detach_decode_sessions()
        old_win.agent_link_manager = None
        old_win.proactive_watcher = None
        # 主窗热切换才换托盘（进程级单托盘）；非主窗热切换不动共享托盘。
        self._build_window(character_id, lib=lib, build_tray=(self is self.shell.instance))
        if self.enable_chat:
            for chat_window in (self.legacy_chat_window, self.modern_chat_window):
                if chat_window is not None:
                    chat_window.set_pet_window(self.win)
            # R03：完整聊天窗 + 岛对话气泡走 B07 收敛出的同一条公开 seam（角色
            # 上下文是各自的构造期快照，换角色后不重指就还是旧角色）；快速对话
            # 气泡是本实例自己持有的那一只（overlay 拓扑下挂在 sprite 壳上），
            # 另有自己的公开 seam，一并同步——否则切完角色在这两处开口仍是旧
            # 角色的会话与系统提示。
            self.sync_chat_character(character_id)
            quick_bubble = getattr(self, "quick_chat", None)
            quick_sync = getattr(quick_bubble, "switch_character", None)
            if callable(quick_sync):
                quick_sync(character_id)
        if getattr(self.shell, "island", None) is not None:
            self.shell.island.refresh_from_config()
        # P1-4：任一窗切换后刷新托盘菜单（per-window 区闭包指向新窗，防陈旧窗）
        self.shell._refresh_tray_menu()

    def sync_chat_character(self, character_id: str) -> None:
        """把新角色同步到本窗的聊天面（B07：overlay 主宠切角色后由 sprite 壳调用）。

        本窗的聊天面（完整聊天窗 + 进程级岛对话气泡）各自持一份角色快照，只换
        素材库不会更新它们——切完角色再开口仍是旧角色的会话/头像/系统提示。这里
        收敛成一条公开 seam，overlay 路径（``OverlayShell.switch_character``）
        与捕获模式（``PetInstance.switch_character``）的对齐口径就是它。

        快速对话气泡不在此列：它随渲染拓扑挂在各自宿主上（overlay 是 sprite 壳的
        ``_quick_chat``，legacy 是本实例的 ``quick_chat``），由持有点自己同步。

        **先刷新 slot0 读视图，再同步已建面**（F01）：本实例的 ``config`` 是那条
        slot0 路由视图（``SlotRoutedConfig(..., 0)``），它读一次就把档案快照缓存住
        ——而"完整聊天窗构造"读的就是它（``ChatWindow(self.config, self.config.get(
        'character'))``）。只同步**已存在**的聊天面并不够：尚未构造的岛对话气泡在
        首次点岛时按 ``AppShell._chat_identity_config()`` 取角色，缓存里的旧角色
        没人作废，那条路径就会进旧角色的会话/人设（2026-10-10 终审 F01，冷启动
        直读磁盘的用例覆盖不到这条暖缓存路径）。刷新只作废只读快照，表单草稿与
        钉住的编辑代次都不受影响（见 ``SlotRoutedConfig.refresh_profile_view``）。
        """
        if not self.enable_chat:
            return
        refresh = getattr(self.config, "refresh_profile_view", None)
        if callable(refresh):
            refresh()
        for chat_window in (self.legacy_chat_window, self.modern_chat_window):
            if chat_window is not None:
                chat_window.switch_character(character_id)
        island_chat = getattr(self.shell, "island_chat", None)
        if island_chat is not None:
            island_chat.switch_character(character_id)

    def _apply_spawn_offset(self) -> None:
        """让新孵化的桌宠与母桌宠错开，避免两个窗口完全重叠。

        批5.2：偏移量改为实例属性（显式传递），不再读进程级
        DSH_PET_SPAWN_OFFSET_INDEX 环境变量——单进程多窗下环境变量是
        进程级的，无法区分各窗（§1.2）。
        """
        if self.win is None:
            return
        index = self._spawn_offset
        if index <= 0:
            return
        scr = self.win.screen_available()
        if scr is None:
            return
        available = scr.availableGeometry()
        horizontal = -1 if self.win.geometry().center().x() > available.center().x() else 1
        vertical = -1 if self.win.geometry().center().y() > available.center().y() else 1
        # 虚拟窗口坐标：贴边状态下实际窗口位置不含绘制偏移，错开要按
        # 角色自然位置算；未支持统一出口的窗口回退实际位置（= 改造前）。
        vp_fn = getattr(self.win, '_virtual_pos', None)
        base = vp_fn() if callable(vp_fn) else QPoint(self.win.x(), self.win.y())
        x = base.x() + horizontal * 48 * index
        y = base.y() + vertical * 32 * index
        mover = getattr(self.win, '_move_window_towards', None)
        if callable(mover):
            mover(x, y)  # 统一出口自带工作区钳位（含小屏兜底）
            return
        # 小屏（可用区比窗口还窄/矮）时上界 < 下界，min/max 会互相打架把
        # 窗口推出屏幕外；先判边界再钳制。
        max_x = available.right() - self.win.width() + 1
        max_y = available.bottom() - self.win.height() + 1
        x = available.left() if max_x < available.left() else min(max(x, available.left()), max_x)
        y = available.top() if max_y < available.top() else min(max(y, available.top()), max_y)
        self.win.move(x, y)

    # ------------------------------------------------------------ 聊天窗
    def _chat_host(self):
        """完整聊天窗的宿主：主窗优先；overlay 拓扑退回 sprite 壳的 pet 形适配。

        overlay 拓扑 ``self.win`` 恒为 None（不建 PetWindow），不退回宿主则
        ``open_chat*`` 直接 return → 菜单「AI 对话」与快速气泡「全文见聊天窗」都
        点了没反应（完整聊天窗完全不可达）。
        """
        if self.win is not None:
            return self.win
        shell = getattr(self.shell, "_overlay_shell", None)
        if shell is None:
            return None
        return _OverlayChatHost(shell)

    def open_chat(self) -> None:
        """Open the configured chat UI; menus only need this stable dispatcher."""
        if str(self.config.get("chat_ui_style", "modern")) == "classic":
            self.open_legacy_chat()
        else:
            self.open_modern_chat()

    def open_quick_chat(self) -> None:
        """打开快速对话气泡；与完整聊天窗共用会话历史。"""
        if not self.enable_chat:
            return
        # 桌宠全部隐藏：气泡锚不到桌宠，改弹到灵动岛上（岛是对话代理）。
        # singleShot 与下方同口径：Cocoa 菜单跟踪期间延迟到菜单关闭再弹。
        shell = getattr(self, "shell", None)
        if shell is not None and not shell._aggregate_pet_visible() \
                and shell._island_chat_available():
            QTimer.singleShot(0, lambda: shell._show_island_chat(activate=True))
            return
        if self.win is None:
            # overlay 拓扑（恒不建 PetWindow）：气泡锚不到老窗口——改走 overlay 壳
            # 的 sprite 锚定路径（与 sprite 右键菜单同一条），否则岛卡片「聊天」/
            # 托盘「快速对话（气泡）」点了没反应。无壳 = 异常中间态，安静不炸。
            overlay = getattr(shell, "_overlay_shell", None) if shell is not None else None
            if overlay is not None:
                QTimer.singleShot(0, overlay.open_quick_chat)
            return
        # Cocoa 原生 QMenu 跟踪期间 activePopupWidget() 可能为 None，且其
        # 嵌套事件循环会把这个 singleShot 留到菜单关闭后再派发。若是 Qt
        # 自绘 popup，下一层仍通过 _defer_while_popup_active 等待其关闭。
        QTimer.singleShot(0, self._show_quick_chat)

    def _show_quick_chat(self) -> None:
        if not self.enable_chat or self.win is None:
            return
        if self._defer_while_popup_active("quick-chat", self._show_quick_chat):
            return
        from .quick_chat import QuickChatBubble

        if self.quick_chat is None:
            # 缓存单例（关闭只 hide）**有意保留**，与完整聊天窗的「关闭即销毁」
            # 不同：气泡只有 18 个控件、构造后驻留约 33 KiB pet 源码分配，探针
            # （.scratch/chat-window-close/probe_window_lifetime.py）实测第二轮
            # 开关的增量是 0.0 KiB / 0 控件——一次性驻留，不随开关增长，不是泄漏。
            # 换成每次重建反而要给这个高频动作付会话/prompt 重建成本。
            self.quick_chat = QuickChatBubble(self.config, pet_window=self.win)
            self.quick_chat.open_chat_callback = self.open_chat
        else:
            self.quick_chat.pet_window = self.win
            self.quick_chat.settings = self.config.chat_settings()
            self.quick_chat.refresh_session()
        if hasattr(self.win, "set_quick_chat_capture_widget"):
            self.win.set_quick_chat_capture_widget(self.quick_chat)
        self.quick_chat.show_for_pet(self.win)

    def open_legacy_chat(self) -> None:
        host = self._chat_host()
        if not self.enable_chat or host is None:
            return
        if self._defer_while_popup_active("legacy-chat", self.open_chat):
            return
        from .chat.legacy_widgets import ChatWindow
        if not self._chat_window_usable(self.legacy_chat_window):
            self.legacy_chat_window = ChatWindow(
                self.config,
                str(self.config.get('character', catalog.DEFAULT_CHARACTER)),
                pet_window=host,
                notifier=self.shell.system_notify,
                auth_callback=self.open_chat_settings,
            )
            self._own_chat_window(self.legacy_chat_window)
        else:
            self.legacy_chat_window.set_pet_window(host)
        self.chat_window = self.legacy_chat_window
        window = self.legacy_chat_window
        self._present_dialog(window, lambda: window.position_near_pet(host))

    def open_modern_chat(self) -> None:
        host = self._chat_host()
        if not self.enable_chat or host is None:
            return
        if self._defer_while_popup_active("modern-chat", self.open_modern_chat):
            return
        from .chat.widgets import ChatWindow
        if not self._chat_window_usable(self.modern_chat_window):
            self.modern_chat_window = ChatWindow(
                self.config,
                str(self.config.get('character', catalog.DEFAULT_CHARACTER)),
                pet_window=host,
                notifier=self.shell.system_notify,
                auth_callback=self.open_chat_settings,
            )
            self._own_chat_window(self.modern_chat_window)
        else:
            self.modern_chat_window.set_pet_window(host)
        self.chat_window = self.modern_chat_window
        window = self.modern_chat_window
        self._present_dialog(window, lambda: window.position_near_pet(host))

    @staticmethod
    def _chat_window_usable(window) -> bool:
        """缓存槽里那只聊天窗还能用吗（关闭即销毁后槽位会被清空/变悬挂）。

        聊天窗不再是「关闭只隐藏」的常驻单例：关窗即销毁，槽位在 ``finished``
        里被清成 None。这里再判一次 C++ 对象存活是防御——对象可能已被父级
        收走而信号还没到（同 ``open_network_info`` 的收口方式）。
        """
        return window is not None and shiboken6.isValid(window)

    def _own_chat_window(self, window) -> None:
        """把新造的聊天窗挂进本实例的收口链：窗口一收起就清掉缓存槽。

        关窗即销毁（``pet/chat/lifecycle.py``）之后，槽位必须跟着窗口走——
        否则下次打开会去 show 一个已排队删除的对象，而 ``self.chat_window``
        这类裸引用会在死对象上抛 "Internal C++ object already deleted"
        （``sync_look_to_chat`` / 设置页刷新都读它）。
        """
        window.finished.connect(lambda _result=0, window=window: self._forget_chat_window(window))

    def _forget_chat_window(self, window) -> None:
        for attr in ("modern_chat_window", "legacy_chat_window", "chat_window"):
            if getattr(self, attr, None) is window:
                setattr(self, attr, None)

    def _defer_while_popup_active(self, key: str, callback) -> bool:
        """Avoid constructing a heavy dialog inside QMenu.exec()."""
        if QApplication.activePopupWidget() is None:
            self._pending_dialog_opens.discard(key)
            return False
        if key in self._pending_dialog_opens:
            return True
        self._pending_dialog_opens.add(key)

        def retry() -> None:
            if QApplication.activePopupWidget() is not None:
                QTimer.singleShot(50, retry)
                return
            self._pending_dialog_opens.discard(key)
            callback()

        QTimer.singleShot(50, retry)
        return True

    def _present_dialog(self, dialog, before_present=None, attempt: int = 0) -> None:
        """延迟呈现非模态窗口，直到任何弹出菜单关闭。

        macOS 的右键/托盘菜单是原生 NSMenu 跟踪会话（menu.exec 阻塞期间），
        菜单项动作触发时会话尚未结束，此时新建窗口的 show/raise/activate
        会被 AppKit 抑制——表现为首次点击「AI 设置 / 桌宠设置」无反应，
        需要再点一次（此时窗口实例已存在，直接 show 成功）。
        延迟到菜单关闭后再呈现即可稳定弹出；Qt 自绘菜单（Windows）同样
        覆盖：弹窗仍显示时重试等待。重试 60 次（约 3.6 秒）后放弃，
        防止弹窗长期不消失时无限空转。
        """
        if attempt > 60:
            return
        if not shiboken6.isValid(dialog):
            # 缓存窗口已销毁（聊天窗/待办面板的「关闭即销毁」）：没必要再呈现。
            return
        if QApplication.activePopupWidget() is not None:
            QTimer.singleShot(60, lambda: self._present_dialog(dialog, before_present, attempt + 1))
            return
        if before_present is not None:
            before_present()
        if dialog.isMinimized():
            dialog.showNormal()
        else:
            dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    # ------------------------------------------------------------ 设置
    def open_chat_settings(self) -> None:
        """Open settings without blocking the desktop pet window.

        QDialog.exec() makes the dialog application-modal, which prevents the
        user from dragging or interacting with the pet while editing settings.
        Keep one modeless dialog alive instead, and refresh the chat window
        after the dialog reports an accepted save.
        """
        if not self.enable_chat:
            return
        from .chat.settings_dialog import ChatSettingsDialog
        if self.chat_settings_dialog is None:
            dialog = ChatSettingsDialog(self.config, self.chat_window)
            dialog.setModal(False)
            dialog.setWindowModality(Qt.WindowModality.NonModal)
            dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
            dialog.finished.connect(self._chat_settings_finished)
            self.chat_settings_dialog = dialog
        self._update_bubble_suppression_for_settings()
        self._present_dialog(self.chat_settings_dialog)

    def _chat_settings_finished(self, result: int) -> None:
        dialog = self.chat_settings_dialog
        self.chat_settings_dialog = None
        self._update_bubble_suppression_for_settings()
        if result:
            self._refresh_chat_windows()

    def _refresh_chat_windows(self) -> None:
        """Refresh both independently styled chat windows after shared settings change."""
        for chat_window in (self.legacy_chat_window, self.modern_chat_window):
            if chat_window is not None:
                chat_window.refresh_settings()

    def _update_bubble_suppression_for_settings(self) -> None:
        """任一设置窗口打开/独立设置进程存活时暂停桌宠气泡，避免气泡盖住设置界面。"""
        if getattr(self, "win", None) is None:
            return
        shell = getattr(self, "shell", None)
        # 独立设置进程在跑时主进程没有对话框对象，只能看 shell 上的存活标记：
        # 锁文件存在期由 AppShell 维护（watcher 立即 + 3s 轮询兜底清除）。
        external_settings = bool(getattr(shell, "_settings_child_active", False)) if shell is not None else False
        any_open = (
            getattr(self, "modern_settings_dialog", None) is not None
            or getattr(self, "chat_settings_dialog", None) is not None
            or external_settings
        )
        self.win.set_bubble_suppressed(any_open)

    def open_modern_settings(self, initial_page: str | None = None) -> None:
        # 默认路径：设置页拉到独立进程（关窗即进程退出，OS 回收首开留下的
        # 字体/样式/模块高水位）。只有开关关闭或 startDetached 失败时才回退
        # 下面的进程内路径——功能绝不丢。
        if self._try_open_settings_process(initial_page):
            return
        from .modern_settings_dialog import ModernSettingsDialog
        if self.modern_settings_dialog is None:
            self._dock_icon_before_settings = bool(self.config.get("show_dock_icon", True))
            dialog = ModernSettingsDialog(
                self.config,
                self.win,
                include_ai=self.enable_chat,
                initial_page=initial_page,
                chat_build_capable=self.chat_build_capable,
            )
            dialog.finished.connect(self._modern_settings_finished)
            self.modern_settings_dialog = dialog
        elif initial_page:
            selector = getattr(self.modern_settings_dialog, "select_page", None)
            if callable(selector):
                selector(initial_page)
        self._update_bubble_suppression_for_settings()
        # 在 show 之前定位，避免 Windows 上窗口先显示默认位置再跳走（闪现小窗）
        self._present_dialog(
            self.modern_settings_dialog,
            before_present=self.modern_settings_dialog.move_away_from_pet,
        )

    def _try_open_settings_process(self, initial_page: str | None = None) -> bool:
        """尝试走独立设置进程；True = 已交给独立进程（不要再开进程内对话框）。"""
        shell = getattr(self, "shell", None)
        opener = getattr(shell, "open_settings_process", None)
        if not callable(opener):
            return False
        try:
            try:
                return bool(opener(self, page=initial_page))
            except TypeError:
                # 兼容旧测试桩/宿主：老 opener 只接受 instance。
                return bool(opener(self))
        except Exception:
            logging.exception("独立设置进程链路异常，回退进程内设置页")
            return False

    def _modern_settings_finished(self, result: int) -> None:
        self.modern_settings_dialog = None
        self._update_bubble_suppression_for_settings()
        # 新版设置在关闭时一律落盘（closeEvent 自动保存，「保存并退出」同样走
        # _write_config），因此无论 Accepted/Rejected 都把改动应用到桌宠。
        # 此前只有 Accepted 才刷新：直接 X 关闭时保存生效但桌宠不更新。
        #
        # 表单用独立编辑视图（SlotRoutedConfig.new_form_view），但它每次**成功落盘**都会
        # 作废运行期这份视图的只读快照（SlotRoutedConfig._refresh_runtime_view），所以
        # 这里不必再补一次刷新。
        #
        # 应用链与 watcher 路径共用 AppShell._apply_external_config_change；但
        # 旧测试桩（AppShell.__new__ + 只打桩原内联 seam，没有 _instances）仍走
        # 原内联序列，保证进程内路径的既有测试/时序逐位不变。
        shell = self.shell
        apply_change = getattr(shell, "_apply_external_config_change", None)
        if callable(apply_change) and getattr(shell, "_instances", None) is not None:
            apply_change()
        else:
            if self.win is not None:
                self.win.refresh_pet_settings()
            shell._sync_dynamic_island()
            shell._apply_balance_timer()
            # Phase 1/2：设置保存后按配置同步可选服务（todo 懒启停）与动画预热
            shell._sync_todo_service()
            shell._sync_chime_service()
            shell._sync_festival_service()
            shell._sync_divination_service()
            self._sync_animation_prewarm()
            self._refresh_chat_windows()
            _mac_set_dock_icon_visible(bool(self.config.get("show_dock_icon", True)))
        # 台词可能刚被改动：把还没有本地音频的句子交给后台补齐（开关默认关闭，
        # 关着时这里是 no-op；点了「编辑点击动画绑定」后直接 Esc 关设置也能一起收）
        self.shell.precache_self_talk_voice("设置保存后")
        if (
            getattr(self, "_dock_icon_before_settings", None) is True
            and not bool(self.config.get("show_dock_icon", True))
        ):
            self._hint_dock_hidden_recovery()


    def _dock_hidden_recovery_message(self) -> str:
        return (
            "已隐藏 Dock 图标，桌宠仍会常驻。需要恢复时：点菜单栏托盘图标 → "
            "「桌宠设置」→ 重新勾选「显示 Dock 图标」；找不到桌宠也从托盘菜单「显示 / 隐藏」恢复。"
        )

    def _hint_dock_hidden_recovery(self) -> None:
        if sys.platform != "darwin":
            return
        win = self.win
        if win is not None and callable(getattr(win, "show_bubble", None)) and win.isVisible():
            win.show_bubble(self._dock_hidden_recovery_message(), duration_ms=7000)
            return
        tray = getattr(self.shell, "tray", None)
        if tray is not None and callable(getattr(tray, "showMessage", None)):
            tray.showMessage(
                "桌宠设置",
                self._dock_hidden_recovery_message(),
                QSystemTrayIcon.MessageIcon.Information,
                7000,
            )

    def _sync_animation_prewarm(self) -> None:
        """设置保存后把预热状态同步到当前素材库（幂等）。

        预热开关已并入省电模式（省电 = 闲置降帧 + 不预热）：省电模式开启时
        关闭后台预热，关闭时恢复。上游 PR73 的独立 animation_prewarm_enabled
        键已移除（8MB 首帧预算下其省内存的价值主张不成立）。

        overlay 拓扑没有 PetWindow（``self.win`` 恒 None），素材库挂在 sprite 壳上
        ——漏掉这条回退，设置页保存省电模式后要等下次启动/切角色才生效。
        """
        win = self.win
        if win is not None:
            libs = [getattr(win, "lib", None)]
            visible = None
            is_visible = getattr(win, "isVisible", None)
            if callable(is_visible):
                visible = bool(is_visible())
        else:
            shell = getattr(self.shell, "_overlay_shell", None)
            if shell is None:
                return
            # 主宠 + 已生子宠各自的库（口径同 overlay_shell 的退出收口遍历）
            libs = [getattr(shell, "lib", None)]
            libs.extend(lib for lib in getattr(shell, "_spawned_libs", {}).values())
            visible = None
            is_visible = getattr(shell, "isVisible", None)
            if callable(is_visible):
                visible = bool(is_visible())
        enabled = not bool(self.config.get("idle_low_fps_enabled", False))
        for lib in libs:
            setter = getattr(lib, "set_prewarm_enabled", None)
            if callable(setter):
                setter(enabled, visible=visible)

    # ------------------------------------------------------------ 其它窗口级
    def sync_look_to_chat(self, user_text: str, reply: str) -> None:
        """把「看看屏幕/主动识屏」的问答同步进 AI 对话记录（issue #24）。

        聊天窗口已创建 → 走窗口内同步（含界面即时刷新）；
        聊天窗口从未打开 → 直接写入当前角色最新会话（无则新建），之后再打开
        聊天窗口即可在历史里回看全文——气泡里被省略/分页的内容不再无处可查。
        """
        if not self.enable_chat or not str(reply or "").strip():
            return
        if self.chat_window is not None and hasattr(self.chat_window, "append_look_sync"):
            self.chat_window.append_look_sync(user_text, reply)
            return
        try:
            from .chat.models import ChatMessage
            from .chat.session_store import SessionStore

            store = SessionStore(self.config.dir, getattr(self.config, "instance_id", ""))
            # ``character`` 是每宠键：会话按角色隔离，读主配置会稳定拿到出厂默认
            # 角色，slot≠0 的宠聊天就串到默认角色的会话里（同族缺陷：进程级服务
            # 读每宠键）。身份统一经 ``_identity_config()`` 取当前呈现身份。
            character_id = str(self._identity_config().get(
                "character", catalog.DEFAULT_CHARACTER))
            sessions = store.list(character_id)
            if sessions:
                session = sessions[0]
            else:
                settings = self.config.chat_settings()
                session = store.create(
                    character_id,
                    settings.active_provider,
                    settings.default_system_prompt,
                )
            msgs = [ChatMessage("user", str(user_text)), ChatMessage("assistant", str(reply))]
            synced, _absorbed = store.append_messages(session, msgs)
            if synced is None:
                # 会话已被并发删除等边界：本地兜底（保持旧行为）
                session.messages.extend(msgs)
                store.save(session)
        except Exception:
            logging.exception("同步识屏问答到会话记录失败")

    def _set_autostart(self, enabled: bool, win=None) -> bool:
        ok = autostart_mod.set_enabled(bool(enabled))
        self.config.set("autostart_wanted", bool(enabled))
        self.config.save()
        target = win or self.win
        if target is not None and not ok:
            target.show_bubble("开机自启写入失败，请检查系统登录项或安全软件设置。", duration_ms=6000)
        return ok

    def _check_autostart_wanted(self) -> None:
        if self.config.get("autostart_wanted", False) and not autostart_mod.is_enabled() and self.win is not None:
            self.win.show_bubble("检测到开机自启已被系统或安全软件关闭，可在设置中重新启用。", duration_ms=7000)

    def _notify_pet_hidden(self) -> None:
        """用户主动隐藏桌宠后弹托盘提示，指明恢复入口。

        批5.2a：灵动岛按聚合可见态同步；非主窗（多窗）的提示文案指向
        托盘菜单里的「显示 / 隐藏 [slot-N]」（P2-5 消除误导）。
        """
        if getattr(self.shell, "island", None) is not None:
            self.shell.island.set_pet_visible(self.shell._aggregate_pet_visible())
        if self.shell.tray is None:
            return
        if self is not self.shell.instance:
            message = "点击托盘菜单中该窗口的「显示 / 隐藏」即可恢复。"
        else:
            if sys.platform == "darwin" and not bool(self.config.get("show_dock_icon", True)):
                message = "点击托盘菜单「显示 / 隐藏」即可恢复。"
            else:
                message = "点击托盘图标或 Dock 图标即可恢复。"
        self.shell.tray.showMessage(
            "桌宠已隐藏",
            message,
            QSystemTrayIcon.MessageIcon.Information,
            4000,
        )

    def _request_exit_window(self) -> None:
        """窗级「退出这只」：委托 AppShell 收口本窗资源（批5.2）。"""
        if self.win is None:
            return
        self.shell._on_window_exit_requested(self)


class AppShell:
    """进程级外壳 —— 托盘、灵动岛、dock 菜单、更新、余额、系统通知、碰撞会话、broker。

    批5.1（纯重构）拆自原 PetApp 的「进程级」半边，行为逐位不变；批5.2
    spike 扩成多窗集合（``self.instances``），``self.instance`` 仍是主窗
    （列表头）。aboutToQuit 收口在 ``_on_about_to_quit``，含窗级/进程级
    资源的分段释放（窗级字段与进程级 broker/碰撞/permanent writer 分开，
    见 §2.2-E2 / R5）。
    """

    #: 类级默认值：测试用 ``__new__`` 绕开 __init__ 拼壳时按「构建带聊天」处理
    #: （同 platform_passthrough 的 _applied_state 惯例）。
    _chat_build_capable: bool = True

    def __init__(self, app: QApplication, config: Config, enable_chat: bool = True,
                 slot_id: int | None = None,
                 spawn_offset: int = 0, overlay_gate=None) -> None:
        self.app = app
        self.config = config
        # 构建变体能力单独留一份真值：harness 这类「no-chat 变体不出场、但与
        # chat 模块无关」的入口按它判（不能用 enable_chat——那里面还有模块闸）。
        self._chat_build_capable = bool(enable_chat)
        # enable_chat 双闸：构建变体能力（no-chat 包）∧ 功能模块激活（chat 模块）。
        # 进程级单源不变——下游全部回调置 None/早退（:587-595/:784/:856 等）
        # 对两种「不可用」一视同仁，模块关了 AI 对话就是不存在。
        self._enable_chat = bool(enable_chat) and feature_activation_mod.is_active("chat")
        self._slot_id = slot_id
        # 托盘本体（属性访问器在下方：overlay 拓扑回退到壳持有的托盘）
        self._tray: QSystemTrayIcon | None = None
        # 托盘上下文菜单所有权（F5）：_build_tray 每次构建的 QMenu 必须由进程侧
        # 强引用保活——PySide6 下仅靠 tray.setContextMenu 持有 C++ 指针时，Python
        # wrapper 一旦被回收，之后的 act.menu()/contextMenu() 会命中 shiboken 缓存里
        # 已失效的 wrapper（RuntimeError: Internal C++ object already deleted）。
        # 除菜单本体外还必须保活其 QAction wrapper：子菜单的 menuAction 挂在父菜单的
        # actions 列表里，这些 QAction wrapper 被回收会让仍存活且被强引用的 QMenu
        # wrapper 连带失效（_install_tray_menu 会一并快照保活）。
        # 新菜单接管后旧菜单经 _install_tray_menu 显式 deleteLater 释放，不累积泄漏。
        self._tray_menu: QMenu | None = None
        self._tray_submenus: list[QMenu] = []
        self._tray_actions: list = []
        self.dock_menu: QMenu | None = None
        self._notification_click_callback = None
        self._toast_windows: list[DesktopNotification] = []
        self.island = None
        self.island_collision = None  # 果冻墙：岛的静态碰撞体（island_collision.py）
        self.island_chat = None  # 岛对话气泡：桌宠隐藏时的交互面（island_chat.py）
        self._spawned_pet_count = 0
        self._balance_busy = False
        # 静默余额查询（岛卡片展开）独立忙标志与节流时间戳；
        # None = 从未查询过（不用 monotonic 绝对值做回拨算术）
        self._quiet_balance_busy = False
        self._quiet_balance_last: float | None = None
        self._balance_cache = None
        self._balance_bridge = None
        # 天气服务（「查看天气」）：懒创建，进程级单例（多窗共用一份忙门与客户端）
        self._weather_service = None
        self._on_about_to_quit_connected = False
        # overlay 拓扑产品壳（4.1a）：仅 overlay 拓扑构造；捕获模式与显式
        # PET_RENDER_TOPOLOGY=legacy 都走 legacy 单窗，恒为 None
        self._overlay_shell = None
        # D4：桌宠单实例进程门（由 main() 抢到后传入；两个拓扑都有，只有直接
        # 构造 AppShell 的测试路径才会拿到 None）。释放挂在会话结束路径
        # （_on_session_end）与 main() 的 finally。
        self._overlay_gate = overlay_gate
        self._balance_timer = QTimer()
        self._balance_timer.timeout.connect(self.show_balance)
        self._update_bridge = None
        self._balance_cache_path = config.dir / 'balance_cache.json'  # 跨实例共享余额缓存（按 provider 绑定）
        # 待办提醒：进程级单例（多窗共用一个调度器，避免每窗一个定时器重复通知），
        # Phase 1 门控：默认懒创建——配置关闭时不构造、不跑 30s 定时器；关闭且
        # 无面板打开时释放。win 引用在服务 tick 时经本类 win 属性动态读主窗，
        # 角色热切换重建窗口后无需重绑（PR72 上游版挂 PetApp；本分支归 AppShell）。
        self.todo_service = None
        self.todo_panel = None
        # 网络信息弹窗（issue #172）：进程级单例、非模态。数据只在打开时取一次
        # （后台线程），故不需要常驻服务；窗口销毁即置空。
        self.network_info_dialog = None
        if self._todo_wanted():
            self._ensure_todo_service()
        # 语音报时：进程级单例（多窗共用调度器）。默认关闭（2026-09-19 起，
        # 主动打扰型功能改由用户显式开启）→ 启动不创建；开启后跑 20s tick，
        # 设置关闭后 stop 并释放。edge-tts 合成在后台线程，
        # 播放与气泡走 GUI 线程（QtMultimedia + win.show_bubble）。
        self.voice_chime_service = None
        if self._chime_wanted():
            self._ensure_chime_service()
        # 助手回复朗读：只是音频通道的一个使用者（不新起通道），默认关。
        # 关着时不建对象、不订阅 ChatService、不建定时器（与 v4.3.0 行为一致）。
        self.chat_reply_reader = None
        if self._chat_reply_voice_wanted():
            self._ensure_chat_reply_reader()
        # 节日提醒：进程级单例（多窗共用调度器）。**默认关闭** → 不创建服务；
        # 由用户在设置里开启后 _sync_festival_service 才创建并跑 30s tick。
        self.festival_service = None
        if self._festival_wanted():
            self._ensure_festival_service()
        # 4.4b：`experimental_single_process_spawn` 键删除——进程内多窗从
        # "实验开关"升格为**唯一**多宠形态（多进程多宠退役层已删除），
        # 原先依赖该 flag 快照的窗级逻辑（runtime 标记版本化、日志前缀、
        # 退出分派、共享子系统）一律常开/按活窗数派生。
        # D0 门控解绑（PHASE4_DESIGN T3）：overlay 拓扑下 hub 必须**常开**——
        # 多 sprite 同角色若各自建链会退化成 N 路独立 ffmpeg（硬指标③回退）。
        # 拓扑判定唯一入口收口在 overlay_shell.is_overlay_topology（T5 的 dev
        # 环境变量，不进 Config/设置页/schema）。
        # 批5.3：进程级共享解码 hub（同角色帧扇出）——使能门收敛为
        # `experimental_shared_decode` 这一个用户级总闸（flag 快照已删；
        # overlay 拓扑不再需要额外门控，legacy 多窗同样受益）。
        self._decode_hub = DecodeFanoutHub(
            enabled=bool(config.get('experimental_shared_decode', True)))
        # 4.4b（T6「multi_window_shared 不删，常开化」）：进程级共享子系统
        # （agent_link / proactive / 全屏 watcher）**恒建**，各窗经 PetWindow
        # 构造参数引用同一份，崩溃/换角色不重建。sprite 拓扑里共享子系统缺位
        # 即静默缺失（agent_link/主动识屏/全屏 watcher 无人承载）。
        #（位置在 _instances 就绪之后，共享 manager 构造期即遍历窗集合）。
        self._shared = None
        # 每窗容器：批5.1 单进程单窗仅一个；批5.2 spike 扩成多窗集合
        #（self.instance 指主窗 = instances[0]，兼容既有调用面）。
        self._instances: list[PetInstance] = []
        # 批 E：清除子肥鱼链式关闭进行中标记（重复点击忽略，保持幂等）。
        self._clear_spawned_pending = False
        # 设置进程隔离：独立设置进程存活标记 + config 目录 watcher/定时器
        #（懒安装，见 _install_config_watcher）。默认关的键下完全不用它们。
        self._settings_child_active = False
        self._settings_launch_at = 0.0
        # 本进程最近一次拉起的设置页归属实例身份（"" = 主身份 / "slot-N"）：
        # 设置页已开着时据此说清"是哪只宠的设置"，None = 归属未知（外部拉起）。
        self._settings_owner_instance_id = None
        self._config_watcher = None
        self._config_reload_timer = None
        self._settings_watch_timer = None
        self._last_config_signature = None
        from . import pet_profiles
        from .settings_slot_config import SlotRoutedConfig
        from types import SimpleNamespace

        migration = pet_profiles.migrate_slot0_profile(config.dir)
        # slot0 档案不可用（``target-corrupt``）只牵连这一份档案 → **降级该槽**，
        # 不拖死整个应用；其余非 ok 状态涉及全局面/迁移基础设施，照旧硬失败并说清楚
        # （'一份坏档案'与'全局配置坏了'是两种事故，处置也不同：前者保留原文件、
        # 把槽空着等用户显式动作，后者必须停下）。2026-10-10 ops D2。
        self._slot0_profile_unusable = False
        self._slot0_profile_diagnosis = ""
        if not migration.ok:
            if not migration.slot_only_problem:
                raise RuntimeError(str(migration))
            self._mark_slot0_profile_unusable(migration)
        pet_profiles.migrate_active_set(config.dir)
        active_slots = list(pet_profiles.load_active_set(config.dir).slots)
        if self._slot0_profile_unusable:
            # 只在**内存**里摘掉 0：持久化活跃集合只由用户显式生成/关闭触发，
            # 启动期不替用户改盘（也不"自动修复"坏档案）。
            active_slots = [n for n in active_slots if n != 0]
        self._active_pet_slots = active_slots
        identity = SimpleNamespace(dir=config.dir, path=config.dir / "config-slot-0.json",
                                   instance_id="slot-0", data={})
        self.instance = PetInstance(
            self, SlotRoutedConfig(identity, config, 0), enable_chat=self.enable_chat,
            slot_id=0, spawn_offset=0,
        )
        self._instances.append(self.instance)
        from .multi_window_shared import SharedSubsystems

        self._shared = SharedSubsystems(self)  # 4.4b：常开化（T6）
        # 订阅 DSH 统一状态（单读方合并后由共享 Agent 联动管理器转发；收敛逻辑
        # 是 pet/dsh_state.py 的纯函数，读方是 DshMonitor 自己）：
        # thinking/attention 注入与 offline 交互收口在管理器内部完成，这里只
        # 驱动灵动岛的 agent 活跃指示。
        # agent_link 模块未激活时共享容器不构造管理器（``_shared.agent_link``
        # 为 None）：没有 DshMonitor 就没有状态可订，订阅整条跳过。
        shared_link = self._shared.agent_link
        if shared_link is not None:
            shared_link.dsh_state_changed.connect(self._on_dsh_state_changed)
        _LIVE_SHELLS.add(self)

    def _mark_slot0_profile_unusable(self, migration) -> None:
        """slot0 档案不可用（单槽损坏）：**降级该槽**，不拖死整个应用（ops D2）。

        一份 ``config-slot-0.json`` 手改坏/断电半写，此前的处置是
        ``raise RuntimeError`` → 启动失败对话框 → 全局配置、其它槽、提醒、托盘全不可用，
        而对话框里连文件路径都没有。同一类事故在活跃集合上走的是"降级为空继续跑"，
        两条真值策略不一致。

        这里的口径（与规范"不覆盖、不自动修复"并存）：

        - **原文件一个字节不动**：损坏内容可能是用户手改的一手数据，任何"自动修复"都是猜；
        - 该槽在**内存**里退出活跃集合（不落盘），恢复正常后照常复活；
        - 其余槽、全局配置、托盘/恢复入口全部照常；
        - 诊断（**含文件路径**）进日志并留给可见提示，用户不必翻日志才知道出了什么事；
        - **恢复方式按当前拓扑如实说**（见下），不许两种拓扑共用一句"重启"。
        """
        self._slot0_profile_unusable = True
        path = getattr(migration, "profile_path", None)
        # 恢复方式两个拓扑不同，不许一锅端：
        # - overlay 壳的「生成 / 恢复 slot0」会把该槽档案**重新读一遍**，修好文件后
        #   免重启即可原地恢复（实机验证：.scratch/product-close-approved-gui-
        #   20261011/REPORT.md §4.4；同链路的失败气泡早就写着"再试一次"）；
        # - legacy 单窗形态在本进程内不再重读该档案（``spawn_pet`` 被
        #   ``_slot0_profile_unusable`` 拒掉，见 ``app.spawn_pet`` 的拒写分支），
        #   所以那条路只能是重启后按修好的档案回来。
        # 旧文案两边都写"重启即可恢复"，对 overlay 用户是错的口径（实测发现）。
        from .overlay_shell import is_overlay_topology

        recovery = ("修好后点托盘 / 恢复窗的「生成 / 恢复 slot0」即可恢复（重启同样有效）。"
                    if is_overlay_topology() else
                    "修好该文件后重启即可恢复。")
        self._slot0_profile_diagnosis = (
            f"slot0 档案不可信（{migration.status}），已跳过这只桌宠；"
            f"原文件保留未改：{path}。" + recovery)
        logging.error("启动：%s（%s）", self._slot0_profile_diagnosis, migration.reason)

    def slot0_profile_diagnosis(self) -> str:
        """slot0 降级诊断（空串 = 正常）。可见恢复入口/托盘提示用。"""
        return str(getattr(self, "_slot0_profile_diagnosis", "") or "")

    def _save_active_set(self, *, action: str) -> bool:
        """把活跃集合落盘（**用户显式动作**路径：生成 / 恢复 / 关闭）。

        坏文件在这里先备份再覆盖（``on_broken="backup-overwrite"``）：文件已经不可信，
        而"用户显式关闭/生成"是规范里唯一允许改活跃集合的动作——继续 ``refuse`` 会让
        「生成了 → 重启又没了」「关掉了 → 重启还在」永远修不好，UI 里却没有任何解释
        （2026-10-10 ops D1）。失败必须**可见**：日志 + 气泡点名路径，不许继续假成功。
        """
        from . import pet_profiles

        result = pet_profiles.save_active_set(
            self.config.dir, self._active_pet_slots, on_broken="backup-overwrite")
        if result.status != "ok":
            logging.error("%s：活跃集合落盘失败（%s）：%s → %s",
                          action, result.status, result.reason, result.path)
            self._notify_active_set_write_failed(action, result)
            return False
        if result.backup_path is not None:
            logging.warning("%s：原活跃集合文件不可信，已备份到 %s 后重建",
                            action, result.backup_path)
        return True

    def _notify_active_set_write_failed(self, action: str, result) -> None:
        """活跃集合落盘失败的可见反馈（气泡；无宠可冒泡时只剩日志，如实不撒谎）。"""
        text = (f"{action}没能记住：活跃状态文件写入失败，重启后这次改动会丢失。\n"
                f"文件：{result.path}")
        for target in (getattr(self, "_overlay_shell", None),
                       getattr(getattr(self, "instance", None), "win", None)):
            shower = getattr(target, "show_bubble", None)
            if callable(shower):
                try:
                    if shower(text, 9000):
                        return
                except Exception:
                    logging.exception("提示活跃集合落盘失败时冒泡失败")

    def _notify_slot0_profile_unusable(self, action: str) -> bool:
        """slot0 档案不可用时"恢复/生成"被拒的可见反馈（含路径）。返回是否上了屏。

        通道按"谁能真上屏"降级：overlay 壳 / 本窗气泡 → 右下角自绘通知（沿用「系统通知」
        总开关，与 ``_notify_settings_process_busy`` 同口径）。全程只有日志 = 用户点了
        没反应，正是本轮要消灭的那类失败；系统通知被关掉时如实记一条 warning，不假称
        已经提示过。
        """
        text = f"{action} 失败：{self.slot0_profile_diagnosis()}"
        for target in (getattr(self, "_overlay_shell", None),
                       getattr(getattr(self, "instance", None), "win", None)):
            shower = getattr(target, "show_bubble", None)
            if callable(shower):
                try:
                    if shower(text, 9000):
                        return True
                except Exception:
                    logging.exception("提示 slot0 档案不可用时冒泡失败")
        if not bool(self.config.get("system_notifications_enabled", True)):
            logging.warning("slot0 档案不可用，且系统通知已关闭，无法可见提示：%s", text)
            return False
        notify = getattr(self, "system_notify", None)
        if callable(notify):
            try:
                notify("无法恢复桌宠", text)
                return True
            except Exception:
                logging.exception("提示 slot0 档案不可用时发通知失败")
        logging.warning("slot0 档案不可用：%s", text)
        return False

    @property
    def enable_chat(self) -> bool:
        """进程级单源（E2/REVIEW_batch51）：窗口经 PetInstance.enable_chat 转发。"""
        return self._enable_chat

    @property
    def chat_build_capable(self) -> bool:
        """构建变体是否带 AI 聊天（不含功能模块闸）：harness 等按它判出场。"""
        return self._chat_build_capable

    @enable_chat.setter
    def enable_chat(self, value):
        self._enable_chat = bool(value)

    @property
    def slot_id(self) -> int | None:
        """主窗 slot（E2：主窗实例 slot_id 为权威，本属性只读转发）。"""
        inst = getattr(self, 'instance', None)
        if inst is not None and getattr(inst, 'slot_id', None) is not None:
            return inst.slot_id
        return self._slot_id

    @property
    def instances(self) -> list[PetInstance]:
        return self._instances

    # --- 进程级功能（todo 提醒等）的鸭式访问器：转发到主窗实例 ---
    @property
    def win(self):
        """主窗窗口；overlay 拓扑下退回 sprite 壳（PetWindow 形的呈现宿主）。

        三个进程级提醒服务（TodoReminderService / VoiceChimeService /
        FestivalReminderService）经 ``app.win`` 取气泡锚点，只读
        ``show_bubble`` / ``isVisible`` / ``cfg`` / ``_speech_bubble`` /
        ``visible_content_rect`` / ``scale``——OverlayShell 逐条提供同契约
        （见 overlay_shell「PetWindow 形宿主面」段），故 overlay 拓扑
        （``instance.win`` 恒 None）下退回壳；不退回则三条提醒全部落到系统通知
        （报时/节日还不过 system_notifications_enabled 门）。

        legacy 拓扑 ``_overlay_shell`` 为 None → 恒返回 PetWindow，逐位不变。
        """
        inst = getattr(self, 'instance', None)
        win = inst.win if inst is not None else None
        if win is not None:
            return win
        overlay = getattr(self, '_overlay_shell', None)
        if overlay is not None:
            return overlay
        if not hasattr(self, '_empty_pet_host'):
            self._empty_pet_host = _EmptyPetHost(self)
        return self._empty_pet_host

    @win.setter
    def win(self, value) -> None:
        """Legacy compatibility setter routed to the primary instance."""
        inst = getattr(self, 'instance', None)
        if inst is not None:
            inst.win = value

    @property
    def tray(self):
        """托盘对象：legacy = 本进程建的 QSystemTrayIcon；overlay = 壳的托盘。

        overlay 拓扑进程级托盘由 OverlayShell 持有（``self.tray`` 恒 None），
        不看向壳则「隐藏桌宠 → 点托盘恢复」提示与 Dock 隐藏指路都读不到托盘。
        """
        tray = getattr(self, '_tray', None)
        if tray is not None:
            return tray
        return getattr(getattr(self, '_overlay_shell', None), 'tray', None)

    @tray.setter
    def tray(self, value) -> None:
        self._tray = value

    @property
    def modern_settings_dialog(self):
        """转发主窗实例的设置对话框引用（TodoReminderService 气泡抑制判定用）。"""
        inst = getattr(self, 'instance', None)
        return getattr(inst, 'modern_settings_dialog', None) if inst is not None else None

    @property
    def chat_settings_dialog(self):
        """转发主窗实例的对话设置引用（TodoReminderService 气泡抑制判定用）。"""
        inst = getattr(self, 'instance', None)
        return getattr(inst, 'chat_settings_dialog', None) if inst is not None else None

    # ------------------------------------------------------------ 功能门控（待办提醒）
    def _todo_wanted(self) -> bool:
        # 模块闸在行为开关之上：todo 模块未激活时连总开关都不看。本方法是
        # 「要不要有服务对象」的唯一判据（构造、启动同步、设置保存、外部变更
        # 全部经过它），返回 False 即不建服务、不起 30s tick。
        if not feature_activation_mod.is_active("todo"):
            return False
        return bool(self.config.get("todo_reminder_enabled", True))

    def _ensure_todo_service(self):
        """懒创建待办提醒服务（仅在使用待办/打开面板时创建）。"""
        if getattr(self, "todo_service", None) is None:
            self.todo_service = TodoReminderService(self)
        return self.todo_service

    def _sync_todo_service(self) -> None:
        """按配置启停待办提醒服务；关闭且无面板打开时释放服务对象。"""
        if self._todo_wanted():
            service = self._ensure_todo_service()
            timer = getattr(service, "_timer", None)
            if timer is not None and callable(getattr(timer, "isActive", None)) and timer.isActive():
                # 已在运行：设置保存只刷新偏好/条目，不重置 30s tick。
                service.apply_config()
            elif callable(getattr(service, "start", None)):
                service.start()
        elif getattr(self, "todo_service", None) is not None:
            try:
                self.todo_service.stop()
            except Exception:
                logging.exception("停止待办提醒服务失败")
            # 面板持有 app 引用并动态读取 todo_service；面板还开着时保留对象。
            if getattr(self, "todo_panel", None) is None:
                self.todo_service = None

    # ------------------------------------------------------------ 功能门控（语音报时）
    def _festival_speak_wanted(self) -> bool:
        """节日语音是否开启——它复用报时服务的音频通道，因此会连带影响通道生命周期。

        总开关与模块闸都走 ``_festival_wanted``（模块未激活即"不要声音"），
        否则模块停用后光凭 speak 键就能把报时服务拉起来。
        """
        return bool(
            self._festival_wanted()
            and self.config.get("festival_reminder_speak", False)
        )

    def _self_talk_speak_wanted(self) -> bool:
        """点击自言自语朗读是否开启——它同样复用报时服务的音频通道。

        ``self_talk_speak_enabled`` 与 ``click_show_self_talk`` 都在
        ``pet_profiles.PER_PET_KEYS`` 里（**每宠键**）：真值在各自的 slot 档案，
        主配置那份只是 slot0 兜底。读主配置会让"设置页明明打开了、运行期看不到"
        ——音频通道根本不建，点击静默无声、连一行日志都没有（v4.3.3 用户实测：
        设置里勾着「点击台词朗读」，`config-slot-0.json` 也有值，就是不朗读）。
        """
        # 语音模块未激活：朗读是语音提供的增强，文字台词不受影响
        if not feature_activation_mod.is_active("voice"):
            return False
        for cfg in self._self_talk_voice_configs():
            try:
                speaks = bool(cfg.get("self_talk_speak_enabled", False))
                shows = bool(cfg.get("click_show_self_talk", False))
            except Exception:  # noqa: BLE001 - 单个视图读失败不该拖垮通道判据
                continue
            if speaks and shows:
                return True
        return False

    def _divination_speak_wanted(self) -> bool:
        """抽签语音播报是否开启——它同样复用报时服务的音频通道。

        与节日语音同族：通道随报时开关一起被释放时，抽签语音就变成"有气泡没声音"。
        判定走抽签自己的配置清洗（`normalize_divination_config`，非法值回落默认），
        与抽签服务读的同一处实现，不另立第二份真值。

        模块未激活时同样返回 False：服务对象已被停掉/不建，再让 speak 键把共用
        音频通道留住就是"模块关了、声音通道还开着"。
        """
        if not feature_activation_mod.is_active("divination"):
            return False
        from .divination import normalize_divination_config

        cfg = normalize_divination_config(self.config)
        return bool(cfg["enabled"] and cfg["speak"])

    def _todo_speak_wanted(self) -> bool:
        """待办语音播报是否开启——它同样复用报时服务的音频通道。

        与节日语音同族：通道随报时开关一起被释放时，待办提醒就变成"有气泡没声音"
        （开关能存、点了没声的死开关）。总开关关掉时待办服务自己也不跑，故两者都要。
        模块未激活同样算"不要"——否则模块停用后光凭一个 speak 键就能把
        VoiceChimeService（20s tick + edge-tts 线程）拉起来（`_todo_wanted` 已含模块闸）。
        """
        return bool(
            self._todo_wanted()
            and self.config.get("todo_reminder_speak", False)
        )

    def _chime_wanted(self) -> bool:
        # 报时自身开启，或节日语音 / 点击自言自语朗读 / 抽签语音 / 待办语音播报 /
        # 助手回复朗读需要这条音频通道（六者共用一套合成与播放，因此"通道是否存在"
        # 取决于其中之一是否需要）。
        # 语音模块未激活：整条通道不建——其它模块的「念出来」是语音提供的可选
        # 增强，模块没了增强自然失效（文字照出）。
        if not feature_activation_mod.is_active("voice"):
            return False
        return (
            bool(self.config.get("voice_chime_enabled", False))
            or self._festival_speak_wanted()
            or self._self_talk_speak_wanted()
            or self._divination_speak_wanted()
            or self._todo_speak_wanted()
            or self._chat_reply_voice_wanted()
        )

    def _ensure_chime_service(self):
        """懒创建语音报时服务（报时 / 手动触发 / 节日语音播报共用）。"""
        if getattr(self, "voice_chime_service", None) is None:
            service = VoiceChimeService(self)
            # 让位钩子：报时每次到点前先问节日提醒这一分钟要不要说话。
            # 用钩子而非让节日去"抢"通道，是为了与两个 QTimer 的触发先后解耦。
            service.yield_slot = self._chime_should_yield
            self.voice_chime_service = service
        return self.voice_chime_service

    def _chime_should_yield(self, chime_slot: str) -> bool:
        """报时让位判定（注入到 VoiceChimeService.yield_slot）。"""
        service = getattr(self, "festival_service", None)
        if service is None:
            return False
        return service.should_speak_at(chime_slot)

    # ------------------------------------------------- 功能门控（助手回复朗读）
    def _chat_reply_voice_wanted(self) -> bool:
        # 默认关（主动打扰型功能）：关着时不建通道、不订阅、不建定时器，
        # 行为与 v4.3.0 完全一致。voice/chat 任一模块未激活都不建对象、
        # 不订阅（与门控「模块关了不建对象」的口径一致；朗读落点在 voice
        # 层的 ensure_audio_channel 另有一闸）。
        if not (feature_activation_mod.is_active("voice")
                and feature_activation_mod.is_active("chat")):
            return False
        return bool(self.config.get("chat_reply_voice_enabled", False))

    def _ensure_chat_reply_reader(self):
        """懒创建回复朗读器（进程级单例：多窗共用一份「回复完成」订阅）。"""
        if getattr(self, "chat_reply_reader", None) is None:
            from .chat_reply_voice import ChatReplyReader

            reader = ChatReplyReader(
                speak=self._speak_chat_reply,
                channel_idle=self._chat_reply_channel_idle,
                enabled=self._chat_reply_voice_wanted,
            )
            reader.start()
            self.chat_reply_reader = reader
        return self.chat_reply_reader

    def _release_chat_reply_reader(self) -> None:
        """先摘订阅、再由调用方停通道：收口期不再往队列里塞新句子。"""
        reader = getattr(self, "chat_reply_reader", None)
        if reader is None:
            return
        self.chat_reply_reader = None
        try:
            reader.stop()
        except Exception:
            logging.exception("停止助手回复朗读失败")

    def _chat_reply_channel_idle(self) -> bool:
        """注入给朗读器的通道占用判据。

        通道可能尚未创建（报时等全关、只有回复朗读要用）：此时返回 True，让
        ``_speak_chat_reply`` 走 ``ensure_audio_channel`` 懒创建，而不是让第一句
        凭空丢掉。
        """
        service = getattr(self, "voice_chime_service", None)
        if service is None:
            return True
        try:
            return bool(service.channel_idle)
        except Exception:
            logging.exception("读取音频通道空闲状态失败")
            return True

    def _speak_chat_reply(self, sentence: str) -> bool:
        """把一句回复送进音频通道。

        口径与「点击自言自语朗读」一致：``respect_quiet=False``——这是对用户刚发出
        那条消息的回应，属于交互应答，不该在免打扰期间被静默丢弃。
        """
        service = self.ensure_audio_channel()
        if service is None:
            return False
        return bool(service.speak(sentence, log_tag="回复朗读", respect_quiet=False))

    def ensure_audio_channel(self):
        """对外暴露的音频通道（节日提醒语音 / 每日一签复用报时服务的合成与播放）。

        通道可能是**为本次调用才懒创建**的（报时、节日语音、点击朗读都关着，只有
        抽签语音一路要用它），此时服务里的 ``_cfg`` 还是模块默认值——合成后端、
        回退开关、音量都按默认走，用户选的本地后端与音量被无视（真事故：只开抽签
        语音 + 选 SAPI + 关回退 + 音量 0，抽签语音照旧联网 edge 合成、按默认音量出声）。

        未在运行就先补读一次用户配置（与 ``speak_self_talk`` 同一条做法），收在这一
        处是为了不必让每个调用方各自补读。运行中的通道由 ``_sync_chime_service`` 在
        保存时刷新，不在这里重读：重读会清掉报时那一组预合成占位。
        """
        if not feature_activation_mod.is_active("voice"):
            # 语音模块未激活：通道不存在（调用方全部按 None 降级：只出文字/气泡）
            return None
        channel = self._ensure_chime_service()
        if channel is not None and not channel.is_running():
            channel.apply_config()
        return channel

    def self_talk_voice_file(self, text: str) -> Path | None:
        """预缓存台词音频的查找路径；没有就返回 None。

        键与产出侧是**同一个实现**（``self_talk_voice.find_audio`` → ``cached_audio``
        → ``cache_path_for`` / ``cache_path``），查找顺序：当前配置（合成后端 + 音色
        + 语速）解析出的 flavor 缓存 → 经典文本缓存
        ``<配置目录>/self_talk_voice/<md5(文本)[:16]>.<扩展名>``（外部产出脚本那条
        兼容来源）。刻意放配置目录而不是仓库 assets —— 它属于用户数据（换资源包/
        更新不该丢），与报时缓存 voice_chime_cache 同级。

        分成两份实现曾经是隐患：算法漂移没有交叉测试兜着，表现只有"声音怎么变了"；
        只按文本命键则是"试听换了新声音、点击台词还是旧声音"。
        """
        directory = getattr(getattr(self, "config", None), "dir", None)
        if directory is None:
            return None
        return self_talk_voice.find_audio(directory, text, self.config)

    def speak_self_talk(self, text: str = "") -> bool:
        """点击自言自语朗读：把同一句话送进报时服务的音频通道。

        优先播预缓存的本地文件（零延迟、断网可用）；没有缓存才回落到在线合成。

        与 ``say_now`` 同理，通道可能是为本次点击才懒创建的（例如报时关着、
        只想要点击出声），此时服务里的 ``_cfg`` 还是模块默认音色；未在运行就
        先 ``apply_config()`` 读一次用户配置，才能用上选定的音色/语速/音量。

        返回 False 仅表示文本为空或通道不可用；合成/播放失败只记日志——气泡由
        调用方照常展示，"看得见听不见"好过"点一下什么都不发生"。
        """
        stripped = str(text or "").strip()
        if not stripped:
            return False
        try:
            channel = self.ensure_audio_channel()
        except Exception:
            logging.exception("获取音频通道失败，点击自言自语仅出气泡")
            return False
        if channel is None:
            return False
        cached = self.self_talk_voice_file(stripped)
        if cached is not None:
            try:
                if channel.play_file(str(cached), log_tag="点击自言自语"):
                    # 落一条日志：本地播报与"偷偷回落到在线合成"必须能分辨，
                    # 否则文件名对不上时只会表现为"声音怎么变了"却查不出原因。
                    logging.getLogger("dsh-pet-standalone").info(
                        "点击自言自语：播放预缓存台词 %s（本地，不走网络）", cached.name)
                    return True
            except Exception:
                logging.exception("本地台词播放失败，回退在线合成：%s", cached.name)
        try:
            # 运行中的通道配置由 _sync_chime_service 在保存时刷新；未运行才需补读。
            if not channel.is_running():
                channel.apply_config()
            # 交互应答显式豁免免打扰（issue #208：点了就要有回应，与点击台词气泡
            # 同口径）；提醒类播报用 speak() 的默认值，免打扰期间静音。
            channel.speak(stripped, log_tag="点击自言自语", respect_quiet=False)
        except Exception:
            logging.exception("点击自言自语语音播报失败")
            return False
        return True

    def precache_self_talk_voice(self, reason: str = "") -> bool:
        """把还没有本地音频的点击台词（含点击动画绑定台词）交给后台补齐。

        合成走「设置 → 语音报时」同一条链路（``pet/tts`` 注册表 + 同一音色），所以
        试听能出声就等于这里能生成；本机可选的自建 TTS 服务只由 ``DSH_PET_TTS_URL``
        覆盖，不在线会自动落回注册表。为什么必须后台：一句在线合成要几秒（自建服务
        更慢），保存设置或点击那一刻同步做必然卡顿。开关
        ``self_talk_voice_precache_enabled`` 默认关闭——开一次要在本机写几十个音频
        文件，不开的用户不该付这笔合成开销。

        返回是否真的起了新一轮。开关关着返回 False（一个请求都不发）；上一轮没跑完
        也返回 False，但这次请求会被记为**待处理快照**，当前批结束后按它补跑一轮
        （合成期间追加的台词不会错过这一轮）。
        """
        if not self_talk_voice.enabled(self.config):
            # 关着时连每宠名单都不读：slot 档案的读取不该为关掉的功能付费。
            return False
        if not feature_activation_mod.is_active("voice"):
            # 语音模块未激活：不发起任何后台合成
            return False
        try:
            started = self_talk_voice.start_precache(
                self.config, configs=self._self_talk_voice_configs())
        except Exception:
            logging.exception("启动台词语音预缓存失败（不影响点击朗读）")
            return False
        if started:
            logging.getLogger("dsh-pet-standalone").info(
                "已开始后台预缓存台词语音%s", f"（{reason}）" if reason else "")
        return started

    def _self_talk_voice_configs(self) -> list:
        """每只活跃宠的配置视图（台词名单 ``self_talk_texts`` 是每宠键，按 slot 读）。

        两条拓扑各自的"该宠此刻真正在用的那份视图"，不另造读取口径：

        - legacy：逐窗 ``PetInstance.config``（``SlotRoutedConfig``，每宠键路由到
          该 slot 档案）；
        - overlay：子宠是 sprite、没有 PetInstance，视图只有壳的 ``_sprite_config``
          给得出（主 sprite 也是这条，slot0 档案）。

        拿主配置统一读会让 slot≠0 的宠在别人的名单上生成，点击时命中不到缓存、
        悄悄回退在线合成（音色变成另一个人）。
        """
        overlay = getattr(self, "_overlay_shell", None)
        all_sprites = getattr(overlay, "_all_sprites", None)
        sprite_cfg = getattr(overlay, "_sprite_config", None)
        if callable(all_sprites) and callable(sprite_cfg):
            return [sprite_cfg(sprite) for sprite in all_sprites()]
        # ``_instances`` 是底层表（``instances`` 只是它的 property）：``__new__``
        # 造出来的测试桩没有这张表，读不到时当作"没有活跃宠"。
        return [inst.config for inst in getattr(self, "_instances", ())]

    def _sync_chime_service(self) -> None:
        """按配置启停语音报时服务（以及共用通道的回复朗读）；关闭时释放服务对象。"""
        if self._chime_wanted():
            service = self._ensure_chime_service()
            if service.is_running():
                # 已在运行：设置保存只刷新配置，不重置 20s tick。
                service.apply_config()
            else:
                service.start()
            # 回复朗读随通道同生共死：开则建（并订阅），关则先摘订阅。
            if self._chat_reply_voice_wanted():
                self._ensure_chat_reply_reader()
            else:
                self._release_chat_reply_reader()
        else:
            # 顺序有意：先摘订阅再停通道，收口期不会有新句子进队列。
            self._release_chat_reply_reader()
            if getattr(self, "voice_chime_service", None) is not None:
                try:
                    self.voice_chime_service.stop()
                except Exception:
                    logging.exception("停止语音报时服务失败")
                self.voice_chime_service = None

    # ------------------------------------------------------------ 功能门控（节日提醒）
    def _festival_wanted(self) -> bool:
        # 模块闸在行为开关之上：festival 模块未激活时连总开关都不看。
        # 本方法是「要不要有服务对象」的唯一判据（构造、启动同步、设置保存、
        # 外部变更全部经过它）；返回 False 既不建服务、也不跑 30s tick 与启动补提醒。
        if not feature_activation_mod.is_active("festival"):
            return False
        # 总开关默认关闭：主动打扰型功能，升级后不应突然冒出来。
        return bool(self.config.get("festival_reminder_enabled", False))

    def _ensure_festival_service(self):
        """懒创建节日提醒服务（仅在开启提醒/手动触发时创建）。

        pet.festival.service 连带 festival/calendar/data 与
        五份节日文案表，实测 2.95MB Python 堆（tools/import_cost.py）。总开关
        默认关闭（主动打扰型功能），关闭时整套文案表都不该常驻——import 收到
        构造点，开启/试听时按需付一次。
        """
        if getattr(self, "festival_service", None) is None:
            from .festival.service import FestivalReminderService

            self.festival_service = FestivalReminderService(self)
        return self.festival_service

    def _sync_festival_service(self) -> None:
        """按配置启停节日提醒服务；关闭时释放服务对象。"""
        if self._festival_wanted():
            service = self._ensure_festival_service()
            if service.is_running():
                # 已在运行：设置保存只刷新配置，不重置 tick。
                service.apply_config()
            else:
                service.start()
        elif getattr(self, "festival_service", None) is not None:
            try:
                self.festival_service.stop()
            except Exception:
                logging.exception("停止节日提醒服务失败")
            self.festival_service = None
        # 节日语音复用报时服务的音频通道：本开关变化会改变"通道是否需要存在"，
        # 故必须连带同步通道生命周期（关掉节日语音后若报时也关，通道应释放）。
        self._sync_chime_service()

    # ------------------------------------------------------------ DSH 联动门
    def _dsh_tracker_wanted(self) -> bool:
        """DSH 联动的功能门：``agent_link`` 模块激活 ∧ ``agent_link.dsh``（默认关）。

        联动开启时桥接插件才安装（``DshMonitor.install_bridge``，关联动即卸载）、
        DshMonitor（桥目录唯一读方：信号分派 + dsh_state 收敛器共用一份解析）
        才由 ``AgentLinkManager.apply_config`` 拉起；联动没开时它 3s 探端口、
        1.5s 读桥目录全是空转。本方法是该门的单一口径，harness 自启/收口也复用它。

        模块激活是**外层**条件（2026-10-21 期 4c）：agent_link 模块停用时共享
        manager 压根不构造，DshMonitor 也就不存在——但这条单一口径还必须自己
        答 False，否则「模块停用」被下游读成「联动关掉了」，harness 自启/收口
        会各按各的理解走。
        """
        if not feature_activation_mod.is_active("agent_link"):
            return False
        agent_cfg = self.config.get("agent_link", {})
        return bool(agent_cfg.get("dsh", False)) if isinstance(agent_cfg, dict) else False

    # ------------------------------------------------------------ 设置进程隔离
    def _apply_external_config_change(self) -> None:
        """把「配置已在别处落盘」同步到运行期（独立设置进程 / watcher 路径）。

        全局快捷键也在这里重注册：关掉进程隔离时设置页就在本进程里保存、**没有**
        watcher，改键要立刻生效就只能挂在这条共用入口上（watcher 路径同样走这里）。

        与进程内对话框 finished 的应用链同源（PetInstance._modern_settings_finished
        在真实壳上委托到这里）；多实例时扇出到每个 PetInstance 的窗口，而不是
        只看主窗——外部改配置的那个实例不一定是主窗。
        """
        self._apply_global_hotkeys()
        for inst in getattr(self, "_instances", []):
            win = getattr(inst, "win", None)
            if win is not None:
                win.refresh_pet_settings()
        overlay_shell = getattr(self, "_overlay_shell", None)
        if overlay_shell is not None:
            refresh = getattr(overlay_shell, "refresh_settings", None)
            if callable(refresh):
                refresh()  # overlay 拓扑的窗口能力重应用（4.1b）
            # 进程级共享子系统（联动读方 / 主动识屏）必须在这里补一次应用：
            # sprite 世界没有 PetWindow，上面的逐窗 refresh_pet_settings 链
            # （终点 sync_optional_services → manager.apply_config）一次都不会
            # 执行，而 OverlayShell.refresh_settings 不含 agent_link/proactive。
            # 不补的话外部改 agent_link.dsh 只落磁盘、读方照旧运行（overlay 下
            # 设置页/独立设置进程保存也不生效）。legacy 走窗链，这里不执行。
            self._sync_shared_optional_services()
        self._sync_dynamic_island()
        self._apply_balance_timer()
        # Phase 1/2：设置保存后按配置同步可选服务（todo 懒启停）与动画预热
        self._sync_todo_service()
        self._sync_chime_service()
        self._sync_festival_service()
        # 抽签的预合成也随配置收敛：音色/语速一变，缓存键就变了，必须重合。
        self._sync_divination_service()
        # 台词语音预缓存同样在这里收敛：设置页默认跑在独立进程
        # （settings_process_isolation），进程内对话框那条保存链根本不会执行——
        # 少了这一行，用户打开开关并保存后本次运行一个文件都不生成（issue #305）。
        # 开关判定在入口内部，关着时是 no-op；重复触发是单飞的，已补齐时立刻返回。
        self.precache_self_talk_voice("外部保存设置后")
        # 外部配置变更也可能改了 agent_link.dsh（右键菜单开关落盘后同样收敛到
        # 这里）→ 各窗 refresh_pet_settings 链里的 manager.apply_config 会按
        # 同一功能门启停 DshMonitor（桥目录唯一读方）；overlay 拓扑由上面的
        # _sync_shared_optional_services 走同一条 apply_config。
        # 联动被关掉 → 桌宠自拉起的 dsh web 立即收口：它唯一的消费者就是这条
        # 联动管线，关掉后留着只是常驻内存。顺序放在跟踪器停表之后（先停探测再
        # 停服务，免得跟踪器在服务消失的那一刻又报一轮 offline）；只动自拉起
        # 登记表里的 pid，用户手动起的实例不受影响。
        # 进程反查（PowerShell ~10s 超时）与 taskkill（~15s 超时）是秒级阻塞：
        # 本方法跑在配置去抖回调（GUI 线程）上，收口与后续设置同步没有依赖，
        # 放 daemon 线程做，不许卡死所有桌宠共用的 GUI 线程（审查缺口 G2）。
        if not self._dsh_tracker_wanted():
            threading.Thread(target=self._stop_harness_if_still_unwanted,
                             daemon=True, name="pet-harness-stop").start()
        for inst in getattr(self, "_instances", []):
            prewarm = getattr(inst, "_sync_animation_prewarm", None)
            if callable(prewarm):
                prewarm()
        for inst in getattr(self, "_instances", []):
            refresh = getattr(inst, "_refresh_chat_windows", None)
            if callable(refresh):
                refresh()
        _mac_set_dock_icon_visible(bool(self.config.get("show_dock_icon", True)))

    def _sync_shared_optional_services(self) -> None:
        """按配置重应用进程级共享子系统（联动读方 / 主动识屏）。

        内容与 legacy 窗内 ``sync_optional_services``（window_optional_services.py
        :353）逐条对齐——同一份共享实例、同一套门判定，只是调用点从窗挪到应用级：
        overlay 拓扑下 ``instances[].win`` 恒为 None，那条链没有执行者。

        只在 overlay 分支调用一次；legacy 由窗链负责，绝不重复（apply_config
        本身幂等，重复调用不会重启监视器，但共享管理器按「窗口/壳」扇出，
        这里仍以"一次"为准）。
        """
        shared = getattr(self, "_shared", None)
        if shared is None:
            return
        # 主动识屏/Agent 联动模块停用：共享容器里没有对应对象（构造前挡），跳过。
        if shared.proactive is not None:
            shared.proactive.apply_config()
        if shared.agent_link is not None:
            shared.agent_link.apply_config()

    def open_settings_process(self, instance=None, page: str | None = None) -> bool:
        """拉起独立设置进程；True = 已交给独立进程（不得再开进程内对话框）。

        False = 开关关闭或 startDetached 失败，由调用方回退进程内设置页。
        """
        if not bool(self.config.get("settings_process_isolation", True)):
            return False
        self._install_config_watcher()
        if self._settings_process_running():
            # 单实例：已有设置进程在跑（可能不是本主进程拉起的）→ 不再拉起，
            # 但仍按"设置开着"抑制气泡并盯住它的锁文件。
            logging.info("独立设置进程已在运行，不重复拉起")
            self._mark_settings_child(True)
            if not self._settings_launch_pending():
                # 拉起窗口之外 = 用户再次点了「桌宠设置」（可能是另一只宠的）：
                # 静默 return True 会让这次点击表现为"什么都没发生"。
                self._notify_settings_already_open(instance)
            return True
        if self._settings_launch_pending():
            # 刚拉起、子进程还没来得及建锁：连点场景视为已在启动，避免双开。
            self._mark_settings_child(True)
            return True
        if not self._launch_settings_process(instance, page=page):
            return False
        self._settings_launch_at = time.monotonic()
        self._settings_owner_instance_id = _instance_config_identity(instance)
        self._mark_settings_child(True)
        return True

    def _notify_settings_already_open(self, instance) -> None:
        """设置页已开着时的可见反馈（此前是静默 no-op，右击另一只宠像没反应）。

        独立设置进程是**单实例**：进程内多窗共用同一份 Config，所有宠的设置页
        走的都是那一个设置进程，无法再开第二扇窗，也无法从主进程把它前置——
        能做的只有说清现状。文案按归属分三种：同一只（"已经打开"）、另一只
        （"先关闭"）、归属未知（外部拉起的设置进程）。

        走 self-drawn 右下角通知而不是桌宠气泡：设置页开着时气泡本来就被抑制
        （_update_bubble_suppression_for_settings），冒泡必被丢弃；通知沿用
        「系统通知」总开关（与待办提醒的 notify 分支同口径）。
        """
        if not bool(self.config.get("system_notifications_enabled", True)):
            return
        owner = getattr(self, "_settings_owner_instance_id", None)
        requested = _instance_config_identity(instance)
        if owner is not None and owner == requested:
            message = "桌宠设置已经打开（独立设置窗口不会重复打开）：请切换到已打开的窗口。"
        elif owner is not None:
            message = "已有一只桌宠的设置窗口开着：请先关闭它，再打开这只的设置。"
        else:
            message = "桌宠设置已经打开：请先关闭已打开的设置窗口再试。"
        logging.info("设置页已在运行，提示用户：%s", message)
        notify = getattr(self, "system_notify", None)
        if callable(notify):
            try:
                notify("桌宠设置", message)
            except Exception:
                logging.exception("设置页占用提示失败")

    def _settings_process_running(self) -> bool:
        """settings.lock 是否被活着的设置进程持有。

        QLockFile 会把「pid 已死」的残留锁判为陈旧并接管，因此设置进程崩溃
        残留的锁文件不会永久堵住设置入口。
        """
        from PySide6.QtCore import QLockFile

        lock_path = self.config.dir / "settings.lock"
        lock = QLockFile(str(lock_path))
        lock.setStaleLockTime(30000)
        try:
            if lock.tryLock(0):
                lock.unlock()
                return False
        except Exception:
            logging.exception("探测设置进程锁失败")
            return True  # 探测异常按"已在运行"保守处理，宁可不开第二份
        if not lock_path.exists():
            # 拿不到锁但锁文件并不存在 = 目录不可写之类的环境错误，不是"已有设置
            # 进程"；否则用户会彻底打不开设置页。
            logging.warning("设置进程锁探测失败且锁文件不存在：%s", lock_path)
            return False
        return True

    def _settings_launch_pending(self) -> bool:
        """刚拉起独立设置进程的启动窗口（子进程建锁前的连点保护）。"""
        started = float(getattr(self, "_settings_launch_at", 0.0) or 0.0)
        return bool(started) and (time.monotonic() - started) <= 5.0

    @staticmethod
    def _spawn_settings_process(program, arguments, working_dir, environment) -> bool:
        """startDetached 独立设置进程，带着**只给子进程**的那份环境。

        单独一层是为了让"注入的环境到底有没有交给子进程"能被点到（Qt 的实例方法
        在测试侧替换不掉，真起一个设置窗又不合适），实现本身是 Qt 的标准 detach
        路径：实例 ``QProcess`` + ``startDetached()``，与静态重载同语义（子进程与
        父进程生命周期解耦；父进程的环境一个字节都不改）。
        """
        from PySide6.QtCore import QProcess

        process = QProcess()
        process.setProgram(program)
        process.setArguments(list(arguments))
        process.setWorkingDirectory(working_dir)
        process.setProcessEnvironment(environment)
        return bool(process.startDetached())

    def _launch_settings_process(self, instance=None, *, page: str | None = None) -> bool:
        """startDetached 独立设置进程；冻结包与源码运行分流。

        源码运行要走 `-m pet`（工作目录取仓库根），冻结包直接复用 exe 的参数
        分流入口（--settings）——与 --uninstall-cleanup 同一范式。

        子进程还带着一条 ``DSH_PET_PARENT_TOPOLOGY``：**本进程运行中的拓扑**
        （``overlay`` / ``legacy``）。设置页要按拓扑分流（子宠页的非 per-slot 键该
        写主配置还是 slot 文件），而它自己只能看到磁盘——磁盘上的捕获开关是**重启级**
        输入，用户勾了没重启时那个值描述的是"下一次启动"，不是那个正在跑的主进程。
        传运行中拓扑 = 让"保存成功、实际无效"重新变回"保存即生效"（见
        ``overlay_settings_command`` 的 ``ENV_PARENT_TOPOLOGY``）。
        """
        from PySide6.QtCore import QProcessEnvironment

        from . import overlay_settings_command as settings_command_mod

        if getattr(sys, "frozen", False):
            program = sys.executable
            arguments = ["--settings"]
            workdir = str(Path(sys.executable).parent)
        else:
            program = sys.executable
            arguments = ["-m", "pet", "--settings"]
            workdir = str(Path(__file__).resolve().parent.parent)
        if not program:
            logging.warning("sys.executable 为空，无法拉起独立设置进程")
            return False
        instance_id = _instance_config_identity(instance)
        if page:
            arguments += ["--settings-page", str(page)]
        if instance_id:
            arguments += ["--instance", instance_id]
        environment = QProcessEnvironment.systemEnvironment()
        environment.insert("DSH_PET_INSTANCE", "")
        environment.insert(settings_command_mod.ENV_PARENT_TOPOLOGY,
                           settings_command_mod.runtime_topology_env_value())
        # 本次冻结的功能快照透传给设置进程：盘上未判定时以它为准——新装用户
        # 在首次自然保存前打开设置不会拿到全激活基线（盲审存疑 B）。
        feature_snapshot = feature_activation_mod.snapshot_env_value()
        if feature_snapshot is not None:
            environment.insert(feature_activation_mod.ENV_FEATURE_SNAPSHOT,
                               feature_snapshot)
        try:
            started = self._spawn_settings_process(program, arguments, workdir,
                                                  environment)
        except Exception:
            logging.exception("拉起独立设置进程异常")
            return False
        if not started:
            logging.warning("startDetached 未启动独立设置进程，回退进程内设置页")
        return bool(started)

    def _mark_settings_child(self, active: bool) -> None:
        """记录独立设置进程存活态，并同步各窗气泡抑制与存活轮询。"""
        self._settings_child_active = bool(active)
        timer = getattr(self, "_settings_watch_timer", None)
        if timer is not None:
            if active:
                if not timer.isActive():
                    timer.start()
            else:
                self._settings_launch_at = 0.0
                # 设置页已关闭：归属身份作废（下次占用提示按"外部拉起"口径说）
                self._settings_owner_instance_id = None
                timer.stop()
        for inst in getattr(self, "_instances", []):
            update = getattr(inst, "_update_bubble_suppression_for_settings", None)
            if not callable(update):
                continue
            try:
                update()
            except Exception:
                logging.exception("同步设置期气泡抑制状态失败")

    def _poll_settings_process(self) -> None:
        """设置进程存活轮询：崩溃/漏事件时兜底解除气泡抑制；顺带消费指令。"""
        # 4.4a：D12 指令消费的轮询兜底（目录 watcher 漏事件/网络盘等场景）。
        self._consume_settings_command()
        self._refresh_legacy_profile_if_changed()
        if not bool(getattr(self, "_settings_child_active", False)):
            return
        if self._settings_process_running():
            # 子进程已建锁 = 启动窗口结束：此后锁一消失就可立即解除抑制。
            self._settings_launch_at = 0.0
            return
        if self._settings_launch_pending():
            return
        self._mark_settings_child(False)

    def _consume_settings_command(self) -> None:
        """消费独立设置进程写下的指令（D12；4.4a 起 legacy 拓扑也走这条通道）。

        退役前 legacy 的「一键退出子肥鱼」由设置进程直接 ``child_pet_cleanup``
        （runtime 标记 + taskkill）杀子进程；多进程多宠退役层停用后不存在跨进程
        子宠，统一改「写指令文件 → 主进程消费」。target 为空 = 退出全部子肥鱼；
        否则只退那个 slot 身份（身份已不在登记表 = 空操作，只留日志）。

        **overlay 拓扑下让路**：壳（``OverlayShell._consume_settings_command``）
        独占这条通道。两份消费者盯同一个配置目录、都走 ``consume_command``
        （消费即删），谁先跑谁把指令摘走——而本类的 ``clear_spawned_pets`` /
        slot 身份在 overlay 下都没有目标（子宠是 sprite，不在 ``_instances``
        里），抢到就是静默丢一次用户点击。
        """
        if getattr(self, "_overlay_shell", None) is not None:
            return
        config_dir = getattr(self.config, "dir", None)
        if config_dir is None:
            return
        from . import overlay_settings_command

        command = overlay_settings_command.consume_command(config_dir)
        if command is None:
            return
        if command.get("command") == overlay_settings_command.CMD_PROFILE_RESET:
            if command.get("target") == 0:
                self._apply_legacy_profile(0)
            return
        if command.get("command") == overlay_settings_command.CMD_APPLY_SETTINGS_RESET:
            target = command.get("target")
            if target is None:
                logging.info("收到设置进程指令 → 按默认值对齐主桌宠运行态")
                self.apply_settings_reset(self.instance)
                return
            for inst in list(self._instances):
                slot = overlay_settings_command.slot_from_instance_id(
                    str(getattr(inst.config, "instance_id", "") or ""))
                if slot == int(target):
                    logging.info("收到设置进程指令 → 按默认值对齐 slot-%s 运行态", target)
                    self.apply_settings_reset(inst)
                    return
            logging.info("收到设置进程指令 → slot-%s 已不在活跃清单，跳过", target)
            return
        if command.get("command") != overlay_settings_command.CMD_EXIT_SPAWNED_PETS:
            return  # consume 已按白名单过滤，这里只是防御
        target = command.get("target")
        if target is None:
            logging.info("收到设置进程指令 → 退出全部子肥鱼")
            self.clear_spawned_pets()
            return
        for inst in list(self._instances):
            if inst is self.instance:
                continue
            slot = overlay_settings_command.slot_from_instance_id(
                str(getattr(inst.config, "instance_id", "") or ""))
            if slot == int(target):
                logging.info("收到设置进程指令 → 退出子肥鱼 slot-%s", target)
                self._on_window_exit_requested(inst, defer_heavy_teardown=True)
                return
        logging.info("收到设置进程指令 → slot-%s 已不在活跃清单，跳过", target)

    def _refresh_legacy_profile_if_changed(self):
        if getattr(self, "_overlay_shell", None) is not None:
            return
        signatures = []
        for name in ("config-slot-0.json", "overlay-slot-generations.json"):
            try:
                stat = (self.config.dir / name).stat()
                signatures.append((stat.st_mtime_ns, stat.st_size))
            except OSError:
                signatures.append(None)
        signature = tuple(signatures)
        previous = getattr(self, "_legacy_profile_signature", signature)
        self._legacy_profile_signature = signature
        if signature != previous and not self._apply_legacy_profile(0):
            self._legacy_profile_signature = previous

    def _apply_legacy_profile(self, slot=0) -> bool:
        if slot != 0:
            return True
        from . import window_placement, pet_profiles

        snapshot = pet_profiles.read_slot_snapshot(self.config.dir, 0)
        if snapshot.status != "ok" or pet_profiles._slot_reset_pending(self.config.dir, 0):
            return False
        inst = self.instance
        win = inst.win
        center = None
        if win is not None:
            center = window_placement.virtual_pos(win) + win.stable_body_local_rect().center()
        inst.config.reload()
        character = str(inst.config.get("character", catalog.DEFAULT_CHARACTER))
        if win is not None:
            if character != (win.profile_character or character):
                try:
                    lib = inst._create_library(character)
                    old = win
                    old.agent_link_manager = None
                    old.proactive_watcher = None
                    old.close()
                    inst._build_window(character, lib=lib)
                    win = inst.win
                except Exception:
                    logging.exception("slot0 档案角色资源应用失败")
                    return False
            else:
                win.refresh_pet_settings()
            if center is not None:
                new_center = window_placement.virtual_pos(win) + win.stable_body_local_rect().center()
                win.move(win.pos() + center - new_center)
            win.set_profile_identity(character, inst.config.profile_generation())
        inst.sync_chat_character(character)
        return True

    def apply_settings_reset(self, instance=None) -> None:
        """刷新全局reset结果；宠物档案、位置与active不属于此动作。"""
        self.config.reload()
        self._apply_external_config_change()

    def _install_config_watcher(self) -> None:
        """盯 config.json 所在目录 + 文件，把外部写盘合并进运行期（幂等）。

        盯目录而不是只盯文件：config.save() 用 os.replace 落盘会换 inode，
        QFileSystemWatcher 对消失的路径会停止监视，只盯文件必丢后续事件。
        目录信号不带文件名，故 basename 过滤交给 fileChanged（带路径），
        目录信号负责换 inode 后重新 addPath、以及 settings.lock 出现/消失。
        300ms 去抖：一次保存可能伴随目录多次变动。进程内保存同样触发——
        去抖窗口内合并即可；finished 路径不改成走 watcher，保持原时序语义。
        """
        if getattr(self, "_config_watcher", None) is not None:
            return
        if not bool(self.config.get("settings_process_isolation", True)):
            return
        from PySide6.QtCore import QFileSystemWatcher, QTimer

        try:
            self.config.dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            logging.warning("配置目录不可创建，跳过 config 变更监视：%s", self.config.dir)
            return
        watcher = QFileSystemWatcher()
        watcher.directoryChanged.connect(self._on_config_dir_changed)
        watcher.fileChanged.connect(self._on_config_file_changed)
        if not watcher.addPath(str(self.config.dir)):
            logging.warning("无法监视配置目录：%s", self.config.dir)
            return
        self._config_watcher = watcher
        self._last_config_signature = self._config_signature()
        self._config_reload_timer = QTimer()
        self._config_reload_timer.setSingleShot(True)
        self._config_reload_timer.setInterval(300)
        self._config_reload_timer.timeout.connect(self._on_config_change_debounced)
        self._settings_watch_timer = QTimer()
        self._settings_watch_timer.setInterval(3000)
        self._settings_watch_timer.timeout.connect(self._poll_settings_process)
        self._watch_config_file()

    def _watch_config_file(self) -> None:
        """确保 config.json 在监视列表里（os.replace 换 inode 后被 Qt 自动移除）。"""
        watcher = getattr(self, "_config_watcher", None)
        if watcher is None:
            return
        try:
            path = str(self.config.path)
            if self.config.path.exists() and path not in watcher.files():
                watcher.addPath(path)
        except OSError:
            pass

    def _config_signature(self):
        """(st_ino, mtime_ns, size) 签名：判断 config 是否真的变了（目录信号不带文件名）。

        ``st_ino`` 让原子 ``os.replace`` 落盘换到的新 inode 与上一份区分开——同字节
        数、同时间戳刻度（FAT/exFAT 2s）的改动不再被判重。**非绝对保证**：原位改写、
        inode 号复用、或取不到 inode（部分网络/FUSE 挂载返回 0）时退化为
        ``(mtime_ns, size)``（与改造前同口径，不会更严），故去重门只许偏保守
        （宁可多应用一次，见 ``_on_config_change_debounced``）。
        """
        try:
            stat = self.config.path.stat()
        except OSError:
            return None
        return (stat.st_ino, stat.st_mtime_ns, stat.st_size)

    def _arm_config_reload(self) -> None:
        """重启去抖窗口：窗口内的多次变动合并成一次 reload+应用。"""
        timer = getattr(self, "_config_reload_timer", None)
        if timer is not None:
            timer.start()

    def _on_config_file_changed(self, path: str) -> None:
        if Path(path).name != self.config.path.name:
            return  # 只认本配置文件的 basename（同目录还有 settings.lock 等）
        self._watch_config_file()
        # 盘上内容没变就不重启去抖窗口：同一次 os.replace 的重复通知可能迟到于
        # 首轮应用之后（mac 实测一次保存两次应用链），无门就会再应用一次。
        # 签名口径与目录侧（_on_config_dir_changed）同源；新内容照常进窗口。
        if self._config_signature() != getattr(self, "_last_config_signature", None):
            self._arm_config_reload()

    def _on_config_dir_changed(self, path: str) -> None:
        self._watch_config_file()
        self._refresh_legacy_profile_if_changed()
        # 4.4a：D12 指令通道的目录侧消费（原子 os.replace 会在目录里产生事件）。
        self._consume_settings_command()
        if self._config_signature() != getattr(self, "_last_config_signature", None):
            self._arm_config_reload()
        # 锁文件出现 = 设置进程已起来（启动窗口结束）；锁文件消失 = 设置进程
        # 退出（QLockFile 解锁即删文件）→ 立即解除气泡抑制。
        lock_path = self.config.dir / "settings.lock"
        if lock_path.exists():
            self._settings_launch_at = 0.0
        elif (bool(getattr(self, "_settings_child_active", False))
                and not self._settings_launch_pending()):
            self._mark_settings_child(False)

    def _on_config_change_debounced(self) -> None:
        # 先采样再 reload，finally 记**采样值**：应用期间外部落盘的第二次保存不能
        # 被算成"已应用"（那会让它随后的通知全被去重门吞掉）。采样只会偏旧 →
        # 失效方向只可能是多应用一次，不可能是漏。
        sampled = self._config_signature()
        self.config.reload()
        try:
            self._apply_external_config_change()
        except Exception:
            logging.exception("应用外部配置变更失败")
        finally:
            self._last_config_signature = sampled

    def _teardown_config_watcher(self) -> None:
        """释放 watcher 与两个定时器（退出/测试收口共用）。"""
        watcher = getattr(self, "_config_watcher", None)
        if watcher is not None:
            for signal, slot in (
                (watcher.directoryChanged, self._on_config_dir_changed),
                (watcher.fileChanged, self._on_config_file_changed),
            ):
                try:
                    signal.disconnect(slot)
                except (RuntimeError, TypeError):
                    pass
            self._config_watcher = None
        for name in ("_config_reload_timer", "_settings_watch_timer"):
            timer = getattr(self, name, None)
            if timer is None:
                continue
            try:
                timer.stop()
            except RuntimeError:
                pass

    # ------------------------------------------------------------ 启动
    def start(self) -> None:
        # aboutToQuit 只在控制器层绑定一次：角色热切换会重建窗口，逐个
        # connect win.save_position 会在旧窗口延迟销毁后残留失效引用。
        # 统一走 _on_about_to_quit，在信号触发时读取当前有效窗口。
        if not self._on_about_to_quit_connected:
            self.app.aboutToQuit.connect(self._on_about_to_quit)
            self._on_about_to_quit_connected = True
        character_id = str(self.config.get('character', catalog.DEFAULT_CHARACTER))
        logging.info('当前形象: %s', character_id)
        # 拓扑分流（T5/4.1a + T4 Phase A）：唯一读取点是
        # overlay_shell.is_overlay_topology（显式 PET_RENDER_TOPOLOGY 优先，其次
        # 主配置 stream_capture_mode，启动时快照定死）。overlay → 单合成窗产品壳
        # 接管渲染，不建 PetWindow；**捕获模式（stream_capture_mode=true）→ 这里
        # 走 legacy 单窗 PetWindow 路径**（OBS 只能枚举真窗口），OverlayShell
        # 根本不构造 ⇒ 活跃宠清单里的多只桌宠不会自动复活（T4「重启时收拢为主
        # 宠物」），但清单本身不删（关掉捕获模式重启即回来）。
        from .overlay_shell import is_overlay_topology
        overlay_topology = is_overlay_topology()
        # 拓扑落一行日志：捕获模式"勾了但没重启"与"重启后确实切了"在用户侧
        # 只表现为窗口标题/任务栏，支持排查需要一行可 grep 的证据。
        logging.info('渲染拓扑: %s',
                     'overlay(单合成窗)' if overlay_topology else 'legacy(单窗 PetWindow)')
        if overlay_topology:
            from .overlay_shell import OverlayShell
            # 4.3 后半：把进程级共享子系统（agent_link / proactive）注入 sprite
            # 世界的壳——overlay 拓扑下 instances[].win 恒为 None，扇出目标就是
            # 这个壳（见 multi_window_shared.presentation_targets）。D0 已保证
            # overlay 拓扑下 _shared 常建；拿不到时传 None，壳内呈现面静默
            # 空转（绝不因注入缺失阻断启动）。legacy 分支逐行不变。
            shared = getattr(self, "_shared", None)
            self._overlay_shell = OverlayShell(
                self.app, self.instance,
                agent_link_manager=getattr(shared, "agent_link", None),
                proactive_watcher=getattr(shared, "proactive", None))
            # 点击自言自语的朗读走同一条音频通道（AppShell 持有，进程内唯一）；
            # overlay 拓扑没有 PetWindow 可注入（app.py:481 的 legacy 落点是
            # PetInstance._wire_window，那里用 self.shell），本类就是那个
            # AppShell，故直接取 self.speak_self_talk——写 self.shell 会
            # AttributeError（见 _precache 段同款注释）。
            self._overlay_shell.on_self_talk_speak = self.speak_self_talk
            self._overlay_shell.start()
            selector = getattr(self._overlay_shell, "select_slot", None)
            if self._slot_id is not None and callable(selector):
                selector(self._slot_id)
            self._wire_overlay_file_interpret()
        else:
            if 0 in self._active_pet_slots:
                self._create_ui_with_character_fallback(str(self.instance.config.get(
                    'character', catalog.DEFAULT_CHARACTER)))
            else:
                self._build_empty_pet_tray()
            self._refresh_legacy_profile_if_changed()
        # 批5.2a：进程级共享全屏 watcher 在主窗就绪后启动（自省任一窗是否需要，
        # 无需窗——环则空转）；flag 关时 _shared 为 None，no-op。
        if self._shared is not None:
            self._shared.start()
        self._install_macos_dock_menu()
        self._sync_dynamic_island()
        self.instance._apply_spawn_offset()
        self._apply_balance_timer()
        self._sync_todo_service()
        # 先同步节日服务：报时服务在 start() 里会立刻 tick 一次，那一刻就需要能问到
        # "本分钟是否让位"。顺序反了会出现"报时先响、节日后响"从而两者都出声。
        self._sync_festival_service()
        self._sync_chime_service()
        # 每日一签：**启动就同步**，让「抽签中...」与今天那段签文在后台先合好。
        # 挂在点击那一刻已经晚了一拍——实测她点下去之后 1.2 秒合成才完成，
        # 那 1.2 秒就是她说的"合成还是太慢"。必须排在 _sync_chime_service 之后：
        # 预合成要用报时服务那条音频通道。
        self._sync_divination_service()
        # 设置页进程隔离：启动即装 config 目录 watcher，独立设置进程落盘后由它
        # 合并进运行期（开关关闭时不装，完全走旧路径）。
        self._install_config_watcher()
        QTimer.singleShot(3500, self.instance._check_autostart_wanted)
        QTimer.singleShot(4000, self._maybe_autostart_harness)
        # 启动时补一次：点击动画绑定对话框不走设置页保存信号，改动要等下次启动才被
        # 发现（合成一句约 20 秒，放晚一点，别和首帧/动画预热抢资源）。
        QTimer.singleShot(9000, self._precache_self_talk_voice_on_start)
        # issue #111：会话结束（Windows 关机/注销）探测器。必须在窗口就绪后安装
        # ——它要在关机窗口期到来**之前**就位，才能抢在会话拆除前关掉 ffmpeg
        # 派生（否则系统会弹 0xc0000142 阻塞关机）。
        self._install_session_watcher()
        # 全局快捷键：壳已就绪才装（分发目标是壳的三个既有入口），默认全不绑。
        self._install_global_hotkeys()
        self._announce_slot0_profile_problem()

    def _announce_slot0_profile_problem(self) -> None:
        """slot0 档案降级后的**可见**告知（含文件路径与恢复动作）。

        静默降级比硬失败更危险：用户看到的是"我的主桌宠不见了"，只会以为坏了或丢了设置。
        这里在窗口/托盘就绪之后把诊断说出来，并点名"生成 / 恢复 slot0"这条显式入口
        （托盘与无托盘恢复窗都有）——恢复动作仍然只由用户自己做，程序不代劳、不覆盖。
        """
        diagnosis = self.slot0_profile_diagnosis()
        if not diagnosis:
            return
        overlay = getattr(self, "_overlay_shell", None)
        if overlay is not None:
            teller = getattr(overlay, "announce_slot0_profile_problem", None)
        else:
            teller = None
        if callable(teller):
            try:
                if teller(diagnosis):
                    return
            except Exception:
                logging.exception("提示 slot0 档案降级失败（overlay）")
        win = getattr(getattr(self, "instance", None), "win", None)
        shower = getattr(win, "show_bubble", None)
        if callable(shower):
            try:
                shower(f"{diagnosis}\n可用托盘/恢复窗的「生成 / 恢复 slot0」再试。",
                       9000)
                return
            except Exception:
                logging.exception("提示 slot0 档案降级失败（窗口）")
        logging.warning("slot0 档案降级提示无法上屏（无可见桌宠/托盘）：%s", diagnosis)

    def _precache_self_talk_voice_on_start(self) -> None:
        """启动后补一次台词预缓存。

        单独方法而不是塞进 lambda：Qt 槽里抛的异常在 pythonw 下只进 stderr（GUI
        程序没有控制台），表现为"什么都没发生"——本功能的第一个版本写成
        ``lambda: self.shell.…``，而 ``start()`` 属于 AppShell 自己（没有
        ``self.shell``），于是每次启动都静默 AttributeError，预缓存从未运行。
        这里显式兜住并落日志，让失败一定看得见。
        """
        try:
            self.precache_self_talk_voice("启动时")
        except Exception:
            logging.exception("启动时预缓存台词语音失败")

    def _wire_overlay_file_interpret(self) -> None:
        """overlay 拓扑的拖文件解读接线（legacy 走 ``install_file_interpreter``）。

        legacy 由 ``PetWindow.install_file_interpreter`` 注入；overlay 无 PetWindow，
        由壳作宿主（``cfg``/``show_bubble``/``show_alert``/``resolve_alert`` 同名面）。
        文件解读模块停用（含硬依赖 chat 缺失）：**不构造控制器、不接缝**——控制器
        构造即持有 ``ChatService``/``SessionStore``，真跑起来还会往 ``sessions/``
        写会话；拖文件只留吃动画与统计。
        """
        if not feature_activation_mod.is_active("file_interpret"):
            return
        try:
            from .file_interpret import FileInterpretController
            self._overlay_shell.set_file_interpret_offer(
                FileInterpretController(self._overlay_shell).offer)
        except Exception:  # noqa: BLE001 - 可选服务，缺失不阻断启动
            logging.exception("overlay: 拖文件解读接线失败")

    # ------------------------------------------------------------ 全局快捷键
    def _install_global_hotkeys(self) -> None:
        """装系统级热键服务（幂等；非 Windows 或装不上时只保留引用供诊断）。"""
        service = getattr(self, "_hotkeys", None)
        if service is None:
            try:
                from .hotkeys import GlobalHotkeyService

                service = GlobalHotkeyService(self.app)
                self._hotkeys = service  # 先保活：装原生过滤器要求对象常驻
                service.install()
            except Exception:
                logging.exception("安装全局快捷键失败（右键菜单仍可用）")
                return
        self._apply_global_hotkeys()

    def _apply_global_hotkeys(self) -> None:
        """按当前配置重新注册全部热键（改键/启动/外部配置变更后调用）。

        手动视觉（看看屏幕 / 截图提问）模块停用：这两个动作**不注册**——``apply``
        是全量重设，从 bindings 里拿掉即顺带注销旧绑定，留着空转的键只会让用户
        按下去毫无反应。回调落点另有早退兜底（残留绑定/老菜单直达时不再采集）。
        """
        service = getattr(self, "_hotkeys", None)
        if service is None or not getattr(service, "installed", False):
            return
        from .hotkeys import ACTIONS

        vision_active = feature_activation_mod.is_active("vision")
        bindings = {
            action: self.config.get(key) for action, key in ACTIONS.items()
            if vision_active or action not in _VISION_HOTKEY_ACTIONS
        }
        callbacks = {
            "open_chat": self._hotkey_open_chat,
            "look_screen": self._hotkey_look_screen,
            "screen_capture": self._hotkey_screen_capture,
        }
        if not vision_active:
            callbacks = {a: cb for a, cb in callbacks.items()
                         if a not in _VISION_HOTKEY_ACTIONS}
        try:
            failures = service.apply(bindings, callbacks)
        except Exception:
            logging.exception("注册全局快捷键失败（右键菜单仍可用）")
            return
        for action, text, reason in failures:
            logging.warning("全局快捷键未生效：%s（%s）%s", action, text, reason)

    def _hotkey_overlay(self):
        return getattr(self, "_overlay_shell", None)

    def _hotkey_open_chat(self) -> None:
        """AI 对话：与右键「AI 对话」同一条入口（内部已处理 overlay 拓扑的回退）。"""
        opener = getattr(self.instance, "open_chat", None)
        if callable(opener):
            opener()
            return
        quick = getattr(self._hotkey_overlay(), "open_quick_chat", None)
        if callable(quick):
            quick()

    def _hotkey_look_screen(self) -> None:
        """看看屏幕：与右键「看看屏幕」同一条入口（不传 sprite = 主宠）。

        手动视觉模块停用：早退（热键本身不注册，本条防残留绑定/直连回调兜底）。
        """
        if not feature_activation_mod.is_active("vision"):
            return
        look = getattr(self._hotkey_overlay(), "look_at_screen", None)
        if callable(look):
            look()
            return
        legacy = getattr(getattr(self.instance, "win", None), "look_at_screen", None)
        if callable(legacy):
            legacy()

    def _hotkey_screen_capture(self) -> None:
        """截图提问：与右键「截图提问」同一条入口。

        手动视觉模块停用：早退（同 ``_hotkey_look_screen``）。
        """
        if not feature_activation_mod.is_active("vision"):
            return
        capture = getattr(self._hotkey_overlay(), "on_screen_capture", None)
        if callable(capture):
            capture()
            return
        legacy = getattr(getattr(self.instance, "win", None), "on_screen_capture", None)
        if callable(legacy):
            legacy()

    def _install_session_watcher(self) -> None:
        """安装会话结束探测器（幂等；实例属性强引用保活，不跨实例共享）。"""
        if getattr(self, "_session_watcher", None) is not None:
            return
        try:
            self._session_watcher = install_session_watcher(
                app=self.app, on_session_end=self._on_session_end,
            )
        except Exception:
            logging.exception("安装会话结束探测器失败")

    def _on_session_end(self) -> None:
        """会话结束（关机/注销）：冻结各窗并停止全部 ffmpeg reader（issue #111）。

        幂等：重复的会话结束信号（WM_QUERYENDSESSION 之后又有 WM_ENDSESSION、
        Qt 的 commitDataRequest/aboutToQuit）只收口一次。

        只做正常退出路径（``_on_about_to_quit``）不做、且关机场景必需的三件事：
        关 ffmpeg spawn 闸门、冻结窗口动画（``match_shutdown``）、把各窗素材库
        里**已建的全部 clip** 一起停掉（不只是各窗当前在播的那个：圈末软停驻留
        的 clip 仍持有一个存活但空闲的 ffmpeg 进程，关机时一并收口）、留一行日志。

        刻意**不**做会话保存/写盘/槽位解锁：关机时登录会话已在拆除，那些收尾
        既非必需（多开文件锁由操作系统在进程退出时释放）又会拉长清理窗口，
        与「静默、快速、不再派生任何进程」的目标相反。
        """
        if getattr(self, "_session_end_done", False):
            return
        # 「真会话结束」才置位（ops5.5 终审 C2）：SessionWatcher 把 aboutToQuit 也
        # 汇进同一条 arm 回调——那只是正常退出的兜底，不是关机/注销。靠
        # ``_on_about_to_quit`` 先行置下的标记区分（Qt 按连接序调槽，主退出先到）。
        # 不区分的话，:2137/:2185/:2369 三处会把「正常退出」误当「会话结束」跳过。
        if not getattr(self, "_plain_about_to_quit", False):
            self._session_end_done = True
        self._mark_session_ending()
        # D4：会话结束（关机/注销）路径释放桌宠单实例进程门。与下面的
        # 「不做会话保存/写盘/槽位解锁」不冲突——本模块的释放是一次 unlink +
        # 关句柄，O(1)、不派生进程、不写盘；不显式释放则只剩"进程被拆掉后由
        # OS 回收句柄 + 下次启动按 QLockFile pid 陈旧判定接管"这条兜底链。
        self._release_overlay_gate()
        stopped = 0
        for inst in self._instances:
            win = getattr(inst, "win", None)
            match_shutdown = getattr(win, "match_shutdown", None)
            if callable(match_shutdown):
                try:
                    match_shutdown()
                except Exception:
                    logging.exception("会话结束时冻结窗口失败")
            lib = getattr(win, "lib", None)
            stop_all = getattr(lib, "stop_all_clips", None)
            if callable(stop_all):
                try:
                    stop_all()
                    stopped += 1
                except Exception:
                    logging.exception("会话结束时停止素材库 clip 失败")
        logging.info(
            "会话结束：已停止全部 ffmpeg reader（%d 个素材库收口），进入静默退出", stopped,
        )
        # 游戏帧率采集同样是进程级资源（采集子进程 + 它那场独占 ETW 会话），会话结束
        # 必须一并收口——否则桌宠随注销被拆掉时，那场会话与采集进程会留在系统里。
        # ``terminate_session=False``：这条路径不派生任何进程（issue #111：关机窗口期
        # CreateProcess 出来的进程在 DLL 初始化阶段失败，弹出阻塞关机的对话框），
        # 所以只做有界硬停、不发官方终止命令。
        self._teardown_fps_capture(terminate_session=False)

    def _release_overlay_gate(self) -> None:
        """释放桌宠单实例进程门（D4；幂等，未注入门时 no-op）。"""
        gate = getattr(self, "_overlay_gate", None)
        if gate is None:
            return
        try:
            gate.release()
        except Exception:
            logging.exception("释放桌宠单实例进程门失败")

    def _teardown_fps_capture(self, *, terminate_session: bool) -> None:
        """进程级收口游戏帧率采集（有界、幂等、**只动我们自己的会话**）。

        为什么必须在 ``aboutToQuit`` / 会话结束这一层收口：岛的 ``closeEvent`` 与
        岛的 ``aboutToQuit`` 槽只做**退订**（``FpsSlot.release`` → ``unsubscribe``），
        而退出线程是 daemon、收口是**非阻塞**的——解释器可能在采集子进程的收口途中
        就退出了，于是留下"采集进程 + 它那场独占 ETW 会话"。这里补上那一步有界收口
        （``FpsHub.close``：停线程 → 停会话 → 有界 join），并且**只在进程级退出**
        调用：正常交互（隐藏岛 / 换目标 / 关一只宠）走的仍是各岛的非阻塞路径，
        不会被这里的等待吊住。

        只操作**我们自己的**那场会话：会话名进程内唯一（``game_fps.new_session_name``），
        终止走官方 ``--terminate_existing_session`` 指名停它——本模块永不使用
        ``--stop_existing_session``（那是停别人的采集会话）。多宠共用一个宿主，
        所以这里的收口对"全部退出"是恰好一次。

        ``terminate_session``：正常退出（托盘退出 / 关最后一窗）为真——那条路径允许
        派生终止命令，它是唯一能由我们保证释放 ETW 会话的方式；关机/注销为假——那条
        路径的纪律是"静默、快速、不再派生任何进程"（issue #111），只做有界硬停。
        """
        try:
            from .island import fps as island_fps_mod
        except ImportError:  # pragma: no cover - 纯桌宠变体没有岛模块
            return
        try:
            island_fps_mod.reset_shared_hub(terminate_session=terminate_session)
        except Exception:
            # 退出收口是尽力而为：任何一个子系统的失败都不许挡住后面的收尾。
            logging.exception("退出时收口游戏帧率采集失败")

    def _mark_session_ending(self) -> None:
        """置位进程级 ffmpeg spawn 闸门（webm_clip.set_session_ending）。

        覆盖「已进入退出流程、但原生 WM_QUERYENDSESSION 未被观测到」的路径
        （如托盘退出、Qt aboutToQuit、测试直接调收口）。
        """
        # 退出标记与闸门同源（R3-gemini P1）：只走 aboutToQuit 置位会漏掉
        # 会话结束观察路径（:2042），在途 autostart worker 会无视退出继续拉起。
        self._quitting = True
        try:
            webm_clip_mod.set_session_ending(True)
        except Exception:
            logging.exception("置位会话结束闸门失败")


    def _create_ui_with_character_fallback(self, character_id: str) -> None:
        """启动路径创建主窗；配置记住的角色素材目录已被删/搬走（如 DLC 卸载）
        时回退默认角色重试一次，而不是直接弹错退出。默认角色也缺素材则照常
        抛出，由上层弹启动错误。"""
        try:
            self.instance._create_ui(character_id)
        except FileNotFoundError:
            if character_id == catalog.DEFAULT_CHARACTER:
                raise
            logging.warning('角色 %s 素材缺失，回退默认角色 %s', character_id, catalog.DEFAULT_CHARACTER)
            character_id = catalog.DEFAULT_CHARACTER
            self.config.set('character', character_id)
            self.instance._create_ui(character_id)

    def _maybe_autostart_harness(self) -> None:
        """「随桌宠启动 dsh 服务」：主窗就绪后按 ``harness_launch_target`` 拉起。

        目标 auto/service 都是**只拉起 dsh 服务**：全程静默（CREATE_NO_WINDOW
        隐藏控制台 + open_browser=False，不开浏览器、不开界面、无弹窗）——自启的
        本意就是把服务准备好，要界面得用户显式选 web/desktop 目标（菜单里点哪项
        起哪项）。目标是 desktop 时直接打开桌面端应用（用户的主力应用，桌宠退出
        **不会**关它）；auto 已不再解析成 desktop（2026-10 语义收紧：否则每次
        桌宠启动都会把整个桌面端 GUI 拉起来）。

        「本机已有实例」的判定交给 ``launch_harness`` 自己做：它同时会看 web
        端口候选与桌面端单实例；在这里按 web 端口提前 return，会把用户选的
        桌面端目标一起挡掉（桌面端根本不上那个端口）。

        机器级语义：仅主窗就绪时调度一次（进程内新窗不重复触发）。

        门控见 ``_harness_autostart_wanted``（本批收紧：DSH 联动没开不拉）。
        注：``launch_harness`` 本身不带门——菜单「启动 dsh web 界面 / 启动桌面端
        界面」是用户明示动作，必须照常可用。
        """
        if not self._harness_autostart_wanted():
            return

        def _run() -> None:
            try:
                from . import harness_launcher as harness_mod
                # 拉起窗口期竞态（审查缺口 G4）：慢探测期间联动可能已被关掉，
                # 门必须在每个耗时步骤后重核，而不是只在调度时查一次。
                # 正常退出同理（R2 第五缺口）：退出标记置位后绝不再拉起。
                if getattr(self, "_quitting", False):
                    return
                if not self._harness_autostart_wanted():
                    return
                harness_mod.launch_harness(
                    open_browser=False,
                    target=str(self.config.get("harness_launch_target", "auto")),
                    # 探测期间一切「用户已经不想要了」的状态都要能中止拉起：
                    # 退出/会话结束之外，还要算上开关或联动被关——desktop 目标下
                    # 这一步的代价是弹出一个用户刚说不想要的 GUI 窗口。
                    cancel_check=lambda: (
                        getattr(self, "_quitting", False)
                        or getattr(self, "_session_end_done", False)
                        or not self._harness_autostart_wanted()
                    ),
                )
                if getattr(self, "_session_end_done", False):
                    # 关机/注销窗口：只标记不补偿——补偿收口会派生 PowerShell/
                    # taskkill，撞 issue #111「静默、快速、不再派生」纪律（ds/sol R3）
                    return
                if not self._harness_autostart_wanted() or getattr(self, "_quitting", False):
                    # 拉起刚完成联动就被关/进程在退出：立刻收口，不留「关了还在跑」
                    harness_mod.stop_self_launched_harness()
            except Exception:
                logging.exception("随桌宠自动拉起 dsh 服务失败")

        threading.Thread(target=_run, daemon=True, name="pet-harness-autostart").start()

    def _harness_autostart_wanted(self) -> bool:
        """「随桌宠启动 dsh 服务」的功能门：三个条件同时成立才自动拉起。

        1. ``system_tools`` 模块激活（2026-10-21 起替换 enable_chat 的历史误绑）；
        2. 用户显式开了 ``harness_autostart``；
        3. **DSH 联动确实启用**（``agent_link.dsh``，复用 ``_dsh_tracker_wanted``
           这个单一口径）。

        第 3 条是本批的收紧。dsh web 的真实消费者只有 DSH 联动这一条管线：
        桥接插件只在开启联动时安装（关闭时卸载）、``DshMonitor``（桥目录唯一
        读方，状态收敛也并入了它）只在联动开启时运行——联动没开时它没有任何
        消费者，只是一个常驻的 node.exe（实机 41.9MB）。旧口径只看前两条，
        于是"联动全关 + 开了自启"的用户白烧一份内存。

        复用 ``_dsh_tracker_wanted`` 而不是就地再读一遍 config：同一个功能门在两处
        各自实现，早晚会漂移（监视器跟联动走、服务不跟）。
        """
        # harness 属系统工具模块（历史误绑 enable_chat：chat 模块一关它跟着消失，
        # 但 dsh 服务管理与 AI 对话无关）。模块关了绝不自启外部服务。
        if not feature_activation_mod.is_active("system_tools"):
            return False
        if not bool(self.config.get("harness_autostart", False)):
            return False
        return self._dsh_tracker_wanted()

    def _stop_harness_if_still_unwanted(self) -> None:
        """G2 异步收口的门复核：窗口期内联动又被打开，则作废本次收口。

        联动关闭触发的收口是 daemon 线程：PowerShell 反查 + taskkill 有秒级
        延迟，期间用户可能已把联动重新打开——裸调 ``_stop_self_launched_harness``
        会把用户刚恢复的服务杀掉（ds/sol R2 发现的反向竞态）。线程入口先复核。

        **诚实残余（ds/sol R3）**：本复核只覆盖「派发 → 线程入口」一段；
        复核通过后收口慢操作（命令行反查 ~0.7s 上限 10s、taskkill 上限 15s）
        期间重开联动不在防护内——服务会被停一次，且不会自动重拉（自启只在
        主窗就绪时调度一次），用户可从菜单手动启动。把复核嵌进逐 pid 的
        收口循环属于另一次设计变更，本批不夹带。
        """
        if getattr(self, "_session_end_done", False):
            # 关机/注销窗口：纪律是静默、快速、不再派生任何进程操作
            # （与主退出收口的跳过语义一致，ds/sol R3）
            return
        if self._dsh_tracker_wanted():
            return
        self._stop_self_launched_harness()

    def _stop_self_launched_harness(self) -> list[int]:
        """收掉本进程自拉起的 dsh web，返回被终止的 pid（见 harness_launcher 归属收口）。

        只认自拉起登记表 + 终止前命令行复核：用户自己在终端跑着的实例不在表中，
        永远不受影响。这是**无确认**的收口路径（退出 / 联动关闭），宁可漏杀。
        """
        try:
            from . import harness_launcher as harness_mod
            return harness_mod.stop_self_launched_harness()
        except Exception:
            logging.exception("停止自拉起的 dsh 服务失败")
            return []

    def _on_dsh_state_changed(self, from_state: str, to_state: str) -> None:
        """订阅 DSH 统一状态变化（共享 Agent 联动管理器转发；收敛器是纯逻辑，
        读方是 DshMonitor 自身）。AppShell 侧只剩灵动岛的状态点——
        thinking/attention 注入联动管线与 offline 收口常驻气泡都在管理器内部完成。

        转发的是**收敛态原文**（八态），不再压成活跃布尔：状态点五态体系（空闲/
        工作/待确认/完成/错误）与优先级判定在岛内解析（``set_dsh_state``），
        这里不做第二套映射。
        """
        island = getattr(self, "island", None)
        if island is not None and shiboken6.isValid(island):
            island.set_dsh_state(to_state)

    # ------------------------------------------------------------ 退出收口
    def _on_about_to_quit(self) -> None:
        """退出前保存各窗位置并释放资源（全进程「全部退出」语义，R5 切分）。

        aboutToQuit 只绑定一次自本控制器；切换角色会重建桌宠窗口，信号
        触发时读取当前窗口（经 ``self.instance.win``），避免调用已延迟销毁
        的旧窗口。

        R5 切分：**窗级**项（位置/预热/Agent/各窗会话保存/slot 锁/每窗自持的
        碰撞会话）逐窗收口；批5.3 起共享解码 hub 为进程级（shutdown 为 no-op），
        **进程级**收口仅剩 hub ``stop_all()`` 与 ``close_all_writers(permanent=True)``
        各停一次——多窗下任一窗退出不许停进程级资源，只有「全部退出」才收口
        （这也是「退出这只」与「全部退出」的核心差异）。
        """
        # 纯桌宠版（打包排除 ``pet.chat``）里这个模块根本不存在：模块级无条件 import
        # 会让正常退出在**第一步**就 ModuleNotFoundError，而它发生在 ``_mark_session_ending``
        # / ``_quitting`` 等收口之前——位置回写、共享子系统停表、ffmpeg reader 收尾全部
        # 被跳过（2026-10-10 终审「no-chat 变体 normal quit 提前异常」）。改成能力守卫内
        # 的按需 import：没有 chat 就没有会话写盘 worker 要收，跳过是正确答案。
        # issue #111：先关 ffmpeg spawn 闸门，再走正常退出收口——正常退出路径
        # （托盘退出/最后窗口关闭）同样落在关机前后，绝不能在里面再派生 reader。
        # 「正常退出兜底」标记（ops5.5 终审 C2）：本槽先于 SessionWatcher 的
        # aboutToQuit→arm 执行（Qt 按连接序调槽），``_on_session_end`` 凭它区分
        # 「真会话结束」与「正常退出兜底」，只有前者才置 ``_session_end_done``。
        self._plain_about_to_quit = True
        self._mark_session_ending()
        # 退出标记（R2 第五缺口）：在途自动拉起 worker 凭它中止或补偿收口——
        # 否则「退出时登记表为空 → worker 随后 spawn 登记」会留下永远无人收的进程。
        self._quitting = True
        # 窗级收口：逐窗保存位置、停本窗预热与 Agent、提交本窗会话
        for inst in self._instances:
            win = inst.win
            if win is not None:
                try:
                    win.save_position()
                except Exception:
                    logging.exception("退出时保存位置失败")
                try:
                    if getattr(win, 'lib', None) is not None:
                        win.lib.pause_warm()
                except Exception:
                    logging.exception("退出时暂停预热失败")
                # issue #111：供给线程不是预热线程——``pause_warm`` 管不到它，而它
                # 自己派生转换 ffmpeg。退出同样是"再派生进程"的坏时机（与 overlay 的
                # ``_on_about_to_quit`` 同一收口位置），库随窗口销毁时活线程还会被
                # 一起析构（Qt fatal）。库侧自带幂等 + 有界等待 + 超时孤儿兜底。
                try:
                    if getattr(win, 'lib', None) is not None:
                        win.lib.cancel_frameseq_provision()
                except Exception:
                    logging.exception("退出时取消帧序列供给线程失败")
                if getattr(win, 'agent_link_manager', None) is not None:
                    try:
                        win.agent_link_manager.shutdown()
                    except Exception:
                        logging.exception("退出时关闭 Agent 失败")
                # 各聊天窗当前会话提交保存（写盘 worker 将在下方永久关闭）
                for _w in (inst.legacy_chat_window, inst.modern_chat_window, inst.quick_chat):
                    _session = getattr(_w, 'session', None)
                    _store = getattr(_w, 'store', None)
                    if _session is not None and _store is not None:
                        try:
                            _store.save(_session)
                        except Exception:
                            logging.exception("退出前保存会话失败")
            # 批5.2 P1-1：每窗自持 broker，逐窗收口（「全部退出」逐窗停）
            try:
                inst.broker_facade.shutdown()
            except Exception:
                logging.exception("退出时关闭 broker facade 失败")
        # 果冻墙：岛的静态碰撞体随「全部退出」收口（主动 leave 即时移出碰撞世界）
        body = getattr(self, "island_collision", None)
        if body is not None:
            try:
                body.stop()
            except Exception:
                logging.exception("退出时停止灵动岛碰撞体失败")
        # 进程级全局订阅注销：类级 list 长期持有本 shell 强引用，不注销会
        # 阻碍 GC（no-chat 变体无 ChatService，导入守卫与注册处同口径）
        try:
            from .chat.service import ChatService as _ChatService
        except ImportError:
            _ChatService = None
        if _ChatService is not None:
            try:
                _ChatService.unregister_global_finished(self._on_global_chat_finished)
            except Exception:
                logging.exception("退出时注销全局聊天订阅失败")
        # 岛对话气泡（进程级）：**不再**在退出时保存会话（H1）——
        # 完整聊天窗与岛聊天共享同一会话对象，岛聊天的内存快照可能是旧的；
        # 退出时整份覆盖会丢掉已在完整窗口里落盘的新消息。
        # 岛聊天的流式回复在 _finished 回调里通过 append_message 即时落盘
        # （quick_chat.py:424），退出时无需、也不应再覆盖保存。
        # 代价：退出时正在流式输出的岛回复会丢失。
        bubble = getattr(self, "island_chat", None)
        if bubble is not None:
            try:
                bubble.close()
            except Exception:
                logging.exception("退出时关闭灵动岛对话气泡失败")
            self.island_chat = None
        # 会话异步写盘（B8）：全部会话已保存，再永久关闭写盘 worker
        #（关掉后迟到的 queued 回调提交会被明确拒绝）。
        if self.todo_service is not None:
            self.todo_service.stop()
        # 语音报时同为进程级懒服务，退出必须一并停：其无主 QTimer 的 timeout
        # 连接从 Qt C++ 侧强引用住整个对象图（理由同 todo_service，见
        # _shutdown_live_for_tests 注释）；不停则退出期仍在跑 20s tick，且
        # 飞行中的合成线程会经信号桥回 GUI 线程回放、触碰正在析构的窗口。
        # 回复朗读先摘订阅再随通道一起收（它自己还挂着 ChatService 的类级订阅表，
        # 不摘就是退出期往已析构对象上回调）。
        self._release_chat_reply_reader()
        if self.voice_chime_service is not None:
            self.voice_chime_service.stop()
        # 节日提醒同为进程级懒服务，退出必须一并停（理由同 voice_chime_service）。
        if self.festival_service is not None:
            self.festival_service.stop()
        # 退出收口：停掉本进程自拉起的 dsh web（父进程退出不带走 CREATE_NO_WINDOW
        # 的子进程，实机确认桌宠退出后 node 仍活着——只能由宿主自己收）。
        # 只杀自拉起登记表里且命令行复核通过的那个：用户手动起的实例不受影响。
        #
        # 会话结束（关机/注销）路径整段跳过：那条路径的纪律是「静默、快速、不再
        # 派生任何进程」（issue #111 的 0xc0000142 就是关机窗口里派生进程引起的），
        # taskkill 与 Get-CimInstance 同样属于派生，且机器马上断电、收口无收益。
        if not getattr(self, "_session_end_done", False):
            self._stop_self_launched_harness()
        try:
            from .chat import session_store as _session_store

            if not _session_store.close_all_writers(permanent=True):
                logging.warning("退出时会话写盘 worker 未干净关闭")
        except ImportError:
            logging.info("退出收口：本构建没有 chat 模块，跳过会话写盘收口")
        except Exception:
            logging.exception("退出时关闭会话写盘 worker 失败")
        # 批5.2a：进程级共享子系统收口（agent_link / proactive / 全屏 watcher）。
        # 单窗关闭（退出这只）不触发——只有「全部退出」才停共享子系统。
        hotkeys = getattr(self, "_hotkeys", None)
        if hotkeys is not None:
            try:
                hotkeys.uninstall()
            except Exception:
                logging.debug("注销全局快捷键失败", exc_info=True)
        if self._shared is not None:
            try:
                self._shared.stop_all()
            except Exception:
                logging.exception("退出时关闭共享子系统失败")
        # 批5.3：进程级共享解码 hub 收口（全部退出时才停；各窗的源/订阅早已
        # 由窗 closeEvent/_switch 的 shareable_end 逐素材收敛）。
        try:
            self._decode_hub.stop_all()
        except Exception:
            logging.exception("退出时关闭共享解码 hub 失败")
        # 帧率段：停表 + 停采集线程 + **进程级有界收口**（全部退出才收口，与共享
        # 子系统/解码 hub 同一条纪律）。岛自己的 aboutToQuit 槽只退订（非阻塞），
        # 解释器可能在采集子进程与它那场独占 ETW 会话收口完之前就退出——那正是
        # "退出后残留采集进程"的成因，所以这里补上有界收口。
        self._teardown_fps_capture(terminate_session=True)
        # 设置页进程隔离：释放 config 目录 watcher 与两个定时器（无主 QTimer 的
        # timeout 连接会从 Qt C++ 侧强引用住本对象图，不停则阻碍回收）。
        self._teardown_config_watcher()

    @classmethod
    def _shutdown_live_for_tests(cls) -> None:
        """收口测试直接创建、未走 aboutToQuit 的 AppShell（对齐 agent_link 同族防线）。

        只做 Qt 生命周期释放，不改业务状态：
        - 停待办提醒服务定时器（其 ``_app`` 反向强引用 shell，且无主 QTimer 的
          timeout 连接从 Qt C++ 侧强引用住整个对象图，Python gc 回收不掉）；
        - 共享子系统经 ``SharedSubsystems._shutdown_live_for_tests`` 收口；
        - 断开 shell → app 的 aboutToQuit 连接并释放反向引用；
        - 停灵动岛碰撞体定时器并注销全局聊天订阅（同生产收口口径）；
        - 复位**游戏帧率的进程级退出闸门**：测试直接调 ``_on_about_to_quit`` /
          ``_on_session_end``（没走真实进程退出）会把它置位，而它在同一进程里是
          一次性的——不复位，后面任何一个用例都再也建不出采集宿主。

        不做 ``_on_about_to_quit`` 的退出语义（保存位置/永久关闭写盘 worker）：
        那是「全部退出」，测试收口不得触发。
        """
        try:
            from .island import fps as _island_fps

            _island_fps._clear_exit_latch_for_tests()
        except Exception:  # pragma: no cover - 纯桌宠变体没有岛模块
            logging.debug("测试收口复位帧率退出闸门失败", exc_info=True)
        for shell in tuple(_LIVE_SHELLS):
            try:
                service = getattr(shell, "todo_service", None)
                if service is not None:
                    try:
                        service.stop()
                    except Exception:
                        logging.debug("测试收口待办服务失败", exc_info=True)
                    shell.todo_service = None
                if getattr(shell, "_shared", None) is not None:
                    shell._shared.stop_all()
                # 全局热键是**系统级**注册：测试里直接 start() 过的壳不摘，注册会活到
                # 进程结束（后面的用例、甚至开发机上真热键都会被它占着）。
                hotkeys = getattr(shell, "_hotkeys", None)
                if hotkeys is not None:
                    try:
                        hotkeys.uninstall()
                    except Exception:
                        logging.debug("测试收口注销全局快捷键失败", exc_info=True)
                    shell._hotkeys = None
                service = getattr(shell, "voice_chime_service", None)
                if service is not None:
                    try:
                        service.stop()
                    except Exception:
                        logging.debug("测试收口语音报时服务失败", exc_info=True)
                    shell.voice_chime_service = None
                service = getattr(shell, "festival_service", None)
                if service is not None:
                    try:
                        service.stop()
                    except Exception:
                        logging.debug("测试收口节日提醒服务失败", exc_info=True)
                    shell.festival_service = None
                if getattr(shell, "instance", None) is not None:
                    win = getattr(shell.instance, "win", None)
                    lib = getattr(win, "lib", None)
                    if lib is not None:
                        lib.pause_warm()
                if getattr(shell, "_on_about_to_quit_connected", False):
                    try:
                        shell.app.aboutToQuit.disconnect(shell._on_about_to_quit)
                    except (RuntimeError, TypeError):
                        pass
                    shell._on_about_to_quit_connected = False
                # 灵动岛碰撞体 30Hz 定时器与全局聊天订阅（同生产收口口径，
                # 不停会在后续测试里打异常循环/阻碍 GC）
                body = getattr(shell, "island_collision", None)
                if body is not None:
                    try:
                        body.stop()
                    except Exception:
                        logging.debug("测试收口灵动岛碰撞体失败", exc_info=True)
                # 设置页进程隔离：watcher/定时器同属"无主 Qt 对象"一族，收口
                #（不停会让后续测试凭空多一条 3s 轮询，并阻碍对象图回收）。
                teardown_watcher = getattr(shell, "_teardown_config_watcher", None)
                if callable(teardown_watcher):
                    try:
                        teardown_watcher()
                    except Exception:
                        logging.debug("测试收口 config watcher 失败", exc_info=True)
                shell._settings_child_active = False
                try:
                    from .chat.service import ChatService as _ChatService
                    _ChatService.unregister_global_finished(shell._on_global_chat_finished)
                except Exception:
                    pass
                shell._instances = []
            except Exception:
                logging.debug("测试收口 AppShell 失败", exc_info=True)

    def _on_shared_fullscreen(self, hit: bool) -> None:
        """批5.2a：共享全屏 watcher 广播 → 扇出到各窗的 _on_fullscreen_changed。

        逐窗动态遍历（读 _instances 而非绑定某窗），任一窗退出/重建后自动忽略它。
        经窗的公开信号全屏状态回传（避开 window 私有面冻结，见 test_architecture 红线2）。
        """
        if getattr(self, "_shared", None) is None:
            return
        # 免打扰（issue #208）：全屏 = 等效免打扰的唯一来源（不另起探测）。
        # **入场先立闸、退场后结算**，两个落点都不能挪：
        # - 入场（hit=True）在隐藏各窗之前立闸，隐藏动作自身带出的呈现不会漏过门控；
        # - 退场（hit=False）必须等各窗**恢复显示之后**再结算——结束汇报经
        #   ``MultiWindowProxy`` 只投向可见窗，窗口还没回来就结算等于把攒下的
        #   提醒汇报给空气（交付确认兜底会让它下一拍重试，但正序才是常态路径）。
        # 也刻意不走下面那个按 auto_hide_fullscreen 的逐窗过滤：那是自动隐藏的
        # 开关，与 quiet_also_on_fullscreen 是两个独立偏好。
        from . import quiet_mode

        if hit:
            quiet_mode.note_fullscreen(True)
        for inst in self._instances:
            win = inst.win
            if win is None or not shiboken6.isValid(win):
                continue
            # 复审 P1-2：全屏广播按每窗配置过滤——关掉「全屏自动隐藏」的窗
            # 不得被无关广播隐藏/恢复（光标路径的每窗 gate 在窗内已有，
            # 该窗的 _on_cursor_visibility_changed 首行自过滤，无需重复）。
            if not getattr(win, "auto_hide_fullscreen", False):
                continue
            try:
                win.fullscreen_changed.emit(hit)
            except (AttributeError, RuntimeError):
                logging.exception("扇出全屏状态到窗口失败")
        if not hit:
            quiet_mode.note_fullscreen(False)

    def _on_shared_cursor(self, visibility: str) -> None:
        """批5.2a：共享光标可见性广播 → 扇出到各窗的 _on_cursor_visibility_changed。"""
        if getattr(self, "_shared", None) is None:
            return
        for inst in self._instances:
            win = inst.win
            if win is None or not shiboken6.isValid(win):
                continue
            try:
                win.cursor_visibility_changed.emit(visibility)
            except (AttributeError, RuntimeError):
                logging.exception("扇出光标状态到窗口失败")

    def _wire_shared_subsystems(self) -> None:
        """批5.2a：把新窗接入进程级共享子系统（agent_link 联动动作链分发）。

        共享 manager 在 AppShell.__init__ 已创建（此刻尚无窗，set_link_next_provider
        落入空集），新窗出现后经 proxy 重新分发；proactive 经构造参数引用同一份；
        全屏 watcher 经 _on_shared_fullscreen/_on_shared_cursor 动态遍历，无需单独连。
        """
        shared = self._shared
        if shared is None:
            return
        link = shared.agent_link
        if link is None:
            return  # agent_link 模块未激活：管理器不存在，无联动链可接
        try:
            shared.proxy.set_link_next_provider(link._next_busy_anim)
        except (AttributeError, RuntimeError):
            logging.exception("把窗口接入共享 Agent 联动链失败")

    def _sync_dynamic_island(self) -> None:
        """按配置创建/隐藏灵动岛；桌宠隐藏后灵动岛仍可常驻。"""
        island_cfg = self.config.get("dynamic_island", {})
        enabled = bool(island_cfg.get("enabled", True)) if isinstance(island_cfg, dict) else False
        if not feature_activation_mod.is_active("dynamic_island"):
            # 功能模块未激活 = 岛**不创建**（下面的 hide 分支只停定时器，岛对象、
            # 四路采样槽与 FPS 订阅都还留着，那条路径不算停用）。岛自己的
            # ``dynamic_island.enabled`` 是行为开关，两处缺省值本来就不一致
            # （config 默认 True / 设置页默认 False），绝不拿它判模块激活。
            enabled = False
        if not enabled:
            if getattr(self, "island", None) is not None:
                self.island.hide()
            # 4.3：overlay 拓扑摘岛桥（岛停用即撤墙）
            overlay_shell = getattr(self, "_overlay_shell", None)
            if overlay_shell is not None:
                overlay_shell.attach_island(None)
            body = getattr(self, "island_collision", None)
            if body is not None and body.has_local_island:
                body.stop()
            # 本进程无岛 ≠ 岛上没有墙：多进程下 slot 配置只对主进程开岛
            # （子宠进程 enabled=False），但岛在别的进程真实存在——远端
            # 硬墙照样要挂（几何经碰撞快照回喂），否则子肥鱼直接穿岛。
            self._sync_island_collision(island_cfg)
            return
        if getattr(self, "island", None) is None:
            from .island.dynamic_island import DynamicIsland

            self.island = DynamicIsland(self.config)
            # 纯桌宠版（无聊天模块）：hidden_chat 的岛单击路由回退为展开卡片，
            # 防"桌宠隐藏 → 岛点了没反应 → 无法恢复"的死锁。getattr 兼容
            # __new__ 测试桩（property 内 AttributeError 时取默认 True）
            self.island.set_chat_available(bool(getattr(self, "enable_chat", True)))
            # 岛图标默认取鱼本体头像（图片路径不碰 emoji 字体栈，见 dynamic_island
            # 的 _icon_pixmap 注释）；帧未就绪时岛侧只画底圈并稍后重试
            self.island.set_icon_provider(self._island_icon_pixmap)
            # 岛名是**当前身份**的名字：角色身份走每宠视图，不是进程级配置
            # （换完角色岛名还停在默认那只，见 _identity_config）
            self.island.set_identity_provider(self._identity_config)
            self.island.clicked.connect(self._toggle_pet_from_island)
            self.island.toggle_pet_requested.connect(self._toggle_pet_from_island)
            self.island.open_chat_requested.connect(self._open_chat_from_island)
            self.island.open_settings_requested.connect(self._open_settings_from_island)
            # 桌宠隐藏时单击岛：弹/收锚定岛的对话气泡（hidden_chat 开启时）
            self.island.chat_requested.connect(self._chat_from_island)
            # 卡片展开 → 静默刷新余额（不冒泡、不播动画，只更新岛卡片）
            self.island.card_expanded.connect(self._quiet_balance_refresh)
            # 进程级聊天完成订阅：AI 回复到达 → 岛播事件动效并记录最近消息。
            # 无聊天功能的打包变体会排除 pet.chat（参照 config.py 的同款守卫），
            # 那里跳过订阅即可，灵动岛本体照常可用。
            try:
                from .chat.service import ChatService
            except ImportError as exc:
                if str(getattr(exc, "name", "") or "").startswith("pet.chat"):
                    ChatService = None  # 无聊天打包变体：跳过订阅，岛本体照常
                else:
                    raise
            if ChatService is not None:
                ChatService.register_global_finished(self._on_global_chat_finished)
        self.island.refresh_from_config()
        # 批5.2a：灵动岛按**聚合**可见态同步（任一窗可见 = 可见），替代只看主窗。
        self.island.set_pet_visible(self._aggregate_pet_visible())
        self.island.show()
        # 4.3：overlay 拓扑下岛墙由 sprite 碰撞世界的岛桥接管（attach 幂等）。
        # 岛自己的 collision_enabled 门只能在这里判：旧 body 路径
        # （_sync_island_collision）读到该键后走 overlay 分支直接 return，键值被
        # 丢弃，到达桥的唯一通道是本调用点。关着时按岛桥的文档化口径传 None
        # （attach_island(None) = 摘桥）——不建桥，岛 hide/show 也不会复活墙；
        # 键缺失/非 dict 一律按 True（与 _sync_island_collision 的清洗口径一致）。
        overlay_shell = getattr(self, "_overlay_shell", None)
        if overlay_shell is not None:
            wall_enabled = bool(island_cfg.get("collision_enabled", True)) \
                if isinstance(island_cfg, dict) else True
            overlay_shell.attach_island(self.island if wall_enabled else None)
        self._sync_island_collision(island_cfg)

    def _sync_island_collision(self, island_cfg) -> None:
        """果冻墙：按配置创建/启停岛的碰撞体（island_collision.py）。

        本进程直连硬墙：岛作为屏幕边界式位置墙，在统一位置出口
        move_window_towwards 里逐次钳制——身体框任何移动都进不了岛区，
        杜绝采样间隙导致的穿透抽搐；岛被拖到桌宠身上由 on_geometry_changed
        事件驱动推出。
        4.4a：跨进程几何发布/远端复制（attach_publisher / on_remote_snapshot）
        随多进程多宠退役层停用——岛上桌宠的碰撞反馈只在本进程结算。
        """
        enabled = bool(island_cfg.get("collision_enabled", True)) \
            if isinstance(island_cfg, dict) else True
        # 4.3：overlay 拓扑下旧果冻墙整体让路——墙已由 sprite 碰撞世界的
        # 岛桥（pet/island_bridge.py，attach_island 接线）接管；旧 body 的
        # pets_provider 只认 PetWindow（overlay 无窗），且 on_geometry_changed
        # 是单槽，被它抢占后岛桥几何断更
        if getattr(self, "_overlay_shell", None) is not None:
            return
        body = getattr(self, "island_collision", None)
        if not enabled:
            if body is not None:
                body.stop()
            return
        # 岛本身没启用（``self.island is None``）时不该建碰撞体：墙的每一个判据
        # 都要问岛（可见性/几何/模式），没有岛就只有"永远不生效"这一种结果，
        # 却会给每只宠挂上 ``_island_clamp_body`` 钩子——历史上那条钩子里的
        # 判空缺失曾让移动整体静默失效（issue #311）。
        if getattr(self, "island", None) is None:
            body = getattr(self, "island_collision", None)
            if body is not None:
                body.stop()
            return
        if body is None:
            from .island.collision import IslandCollisionBody

            body = IslandCollisionBody(
                self.island, self.config,
                pets_provider=lambda: [
                    inst.win for inst in self._instances if inst.win is not None
                ])
            self.island_collision = body
            if self.island is not None:
                self.island.on_geometry_changed = body.submit
                self.island.on_pet_visibility_changed = body.set_own_pet_visible
        # 4.4a：岛几何的跨进程发布通道（attach_publisher / detach_publisher）
        # 随多进程多宠退役层停用——本进程直连硬墙不再向碰撞世界注册静态成员。
        try:
            body.start()
        except Exception:
            # 岛对象异常（如测试桩无 geometry）时碰撞体降级为不启用，
            # 不影响灵动岛本体功能。
            logging.exception("启动灵动岛碰撞体失败")

    def _open_chat_from_island(self) -> None:
        if not getattr(self, "enable_chat", True):
            # no-chat 打包变体：给可见反馈（岛弹跳），不静默 no-op
            island = getattr(self, "island", None)
            if island is not None and shiboken6.isValid(island):
                island.bump(1.5, 0.0, -1.0)
            return
        # 桌宠全部隐藏：快速气泡锚不到桌宠，改弹到岛上（岛是对话代理）
        if not self._aggregate_pet_visible() and self._island_chat_available():
            self._show_island_chat(activate=True)
            return
        inst = self.instance
        if inst is not None and callable(getattr(inst, "open_quick_chat", None)):
            inst.open_quick_chat()

    # -------------------------------------------------------- 岛对话气泡
    def _island_hidden_chat_enabled(self) -> bool:
        island_cfg = self.config.get("dynamic_island", {})
        if not isinstance(island_cfg, dict):
            return False
        return bool(island_cfg.get("hidden_chat", True))

    def _island_interactive(self) -> bool:
        """岛当前是不是**启用且可见**的交互面。

        ``_sync_dynamic_island`` 关岛走的是 ``hide()`` + 摘桥，**不销毁对象**——
        「对象保留 ≠ 交互面可用」。判据读宿主这一侧的运行态（模块激活快照 +
        本进程内存里的岛启用真值 + 岛窗自己的显隐），不读磁盘：岛开关可能是
        运行中改的（托盘菜单 / 设置子进程落盘后由 config watcher 收口），磁盘上
        那份随时可能滞后一拍。
        """
        if not feature_activation_mod.is_active("dynamic_island"):
            # 功能模块未激活：岛对象根本不该存在（见 _sync_dynamic_island），这里
            # 是同一口径的兜底——判据只读「模块激活 ∧ 岛开关 ∧ 岛窗可见」三件
            # 事，绝不因为「岛对象非空」就当成交互面可用。
            return False
        island = getattr(self, "island", None)
        if island is None or not shiboken6.isValid(island):
            return False
        island_cfg = self.config.get("dynamic_island", {})
        if isinstance(island_cfg, dict) and not bool(island_cfg.get("enabled", True)):
            return False
        try:
            return bool(island.isVisible())
        except (AttributeError, RuntimeError):
            # 测试替身/已销毁的原生窗口：宁可判成不可用（不弹孤窗），也不赌
            return False

    def _island_chat_available(self) -> bool:
        """岛对话气泡可用：聊天功能在 + 岛是启用可见的交互面 + hidden_chat 开。

        关岛之后（运行中改的也算）这里的三个"岛还在"的门都必须同时成立，否则隐藏
        桌宠时的反馈改道/后台回复会锚着岛旧位置弹气泡——一个用户关掉的岛还在屏幕
        上冒泡（2026-10-09 审查 R07）。
        """
        if not getattr(self, "enable_chat", True):
            return False
        if not self._island_interactive():
            return False
        return self._island_hidden_chat_enabled()

    def _chat_identity_config(self):
        """岛对话面的**身份配置视图**（角色与会话的真值 = slot0 档案，规范 §二）。

        共享会话的角色由 **slot0 自己的档案**决定：`slot0` 关闭（留槽）不改变它，
        子宠切自己的角色也不改变它。`AppShell.instance` 的配置面就是那条路由视图
        （``SlotRoutedConfig(..., 0)``）：每宠键（含 ``character``）从 `config-slot-0.json`
        读、缺键取同源默认，全局键（``chat_settings`` / 密钥 / 会话目录身份）仍取主配置。
        这正是"一份会话、一个角色、会话归共享"的落点。

        为什么不能直接用 ``self.config``（全局面）：迁移已经把 slot0 的每宠键从
        ``config.json`` 抽走，全局面上那个 ``character`` 只是**出厂默认的残留**。用它
        构造岛气泡 = 第一次点岛就进默认角色的会话/人设，而完整聊天窗与宠物快速对话走
        的是 slot0 档案——同一个应用里两套角色。（``sync_chat_character`` 只修已存在的
        岛气泡，修不了首次构造；2026-10-10 终审 A02。）

        拿不到实例（精简装配 / 测试替身）时回退全局面。
        """
        config = getattr(getattr(self, "instance", None), "config", None)
        return config if config is not None else self.config

    def _identity_config(self):
        """进程级服务的**角色身份**取源：当前呈现身份的每宠视图。

        ``character`` 属 ``PER_PET_KEYS``，真值在 ``config-slot-N.json``；全局面那个
        ``character`` 是出厂默认的残留（slot0 迁移与 ``Config.save`` 都会把每宠键从
        ``config.json`` 清掉）。所以灵动岛名、托盘「切换角色」勾选、主动识屏的
        ``pet_name`` 三条进程级读点一律经这里取当前身份——不是「没有值」，是**稳定
        读到错的默认值**（2026-10-19 用户反馈：换完角色，灵动岛名还是默认那只）。

        两条拓扑各自的"该宠此刻真正在用的那份视图"，不另造读取口径：

        - overlay：主宠是 sprite，取壳的 ``_sprite_config(主 sprite)``（配置同步边界
          会作废它的快照，换角色后立刻是新值）；
        - legacy：``PetInstance.config`` 那条 slot0 路由视图。

        overlay 壳还没有主 sprite（启动期、slot0 档案被降级、退出收口后）时退回同一条
        slot0 视图：两者读的都是 slot0 档案，不会给出第二个真值。
        """
        overlay = getattr(self, "_overlay_shell", None)
        sprite = getattr(overlay, "sprite", None)
        sprite_config = getattr(overlay, "_sprite_config", None)
        if sprite is not None and callable(sprite_config):
            return sprite_config(sprite)
        return self._chat_identity_config()

    def _show_island_chat(self, *, activate: bool = True,
                          reply_text: str | None = None) -> None:
        """弹出锚定灵动岛的对话气泡（activate=False 为不抢焦点的预览弹出）。"""
        if not self._island_chat_available():
            return
        from .island.chat import IslandChatBubble

        chat_config = self._chat_identity_config()
        if getattr(self, "island_chat", None) is None:
            self.island_chat = IslandChatBubble(chat_config)
            self.island_chat.show_pet_requested.connect(self._show_pets_from_island_chat)
        bubble = self.island_chat
        bubble.open_chat_callback = self._open_full_chat_from_island_chat
        bubble.settings = chat_config.chat_settings()
        bubble.refresh_session()
        bubble.show_for_island(self.island, activate=activate, reply_text=reply_text)

    def _chat_from_island(self) -> None:
        """桌宠隐藏时单击岛：气泡已开则收起，否则弹出（交互式）。

        **没有对话能力的变体（纯桌宠版，打包时排除 `pet.chat`）**走不到气泡：
        岛是桌宠隐藏后唯一的常驻交互面，此时单击直接**把桌宠叫回来**。
        旧行为是发完 `chat_requested` 后被 `_show_island_chat` 的可用性闸门
        静默挡掉，用户点了完全没反应（2026-09-23 修复）。
        """
        if not self._island_chat_available():
            self._show_pets_from_island_chat()
            return
        bubble = getattr(self, "island_chat", None)
        if bubble is not None and shiboken6.isValid(bubble) and bubble.isVisible():
            bubble.close()
            return
        self._show_island_chat(activate=True)

    def _open_full_chat_from_island_chat(self) -> None:
        inst = getattr(self, "instance", None)
        if inst is not None and callable(getattr(inst, "open_chat", None)):
            inst.open_chat()

    def _island_feedback_available(self) -> bool:
        """桌宠隐藏期间灵动岛反馈面是否可用（window 的联动暂停决策探针）。

        可用时隐藏不暂停联动监视器：DSH 事件继续驱动岛反馈气泡；
        不可用（无聊天模块 / 岛未启用 / hidden_chat 关）时照旧暂停省电。"""
        return self._island_chat_available()

    def _island_feedback_bubble(self, text: str, subtitle: str = "",
                                duration_ms: int = 3200) -> bool:
        """桌宠隐藏时的反馈气泡改道面（window_alerts.redirect_hidden_bubble 注入调用）。

        DSH 联动状态/提醒等非交互气泡在桌宠隐藏期间改弹到岛对话气泡
        （预览式，不抢焦点，超时自动收回，后到覆盖）。岛对话不可用
        （无聊天模块 / 岛未启用 / hidden_chat 关 / 桌宠其实可见）时返回
        False，调用方维持原丢弃行为。

        岛聊天**有在途请求**时同样返回 False（丢弃本轮反馈）：气泡是同一个，
        预览式改写会把在途请求的 request_id 清掉，真实回答随后被完成回调按
        request_id 静默丢掉（既不显示也不落库）——聊天明显比一条状态气泡重要。
        不排队：这类反馈是"正在想办法/开始干活"的瞬时状态，压到回答之后才送
        等于送达一条过期消息（与 :meth:`_on_global_chat_finished` 的"气泡在场
        则跳过"同一取舍）。"""
        if self._aggregate_pet_visible() or not self._island_chat_available():
            return False
        island = getattr(self, "island", None)
        if island is None or not shiboken6.isValid(island):
            return False
        from .island.chat import IslandChatBubble

        if getattr(self, "island_chat", None) is None:
            self.island_chat = IslandChatBubble(self._chat_identity_config())
            self.island_chat.show_pet_requested.connect(self._show_pets_from_island_chat)
        bubble = self.island_chat
        asker = getattr(bubble, "request_in_flight", None)
        if callable(asker) and asker():
            return False
        bubble.open_chat_callback = self._open_full_chat_from_island_chat
        bubble.show_feedback(island, text, subtitle=subtitle, duration_ms=duration_ms)
        return True

    def _show_pets_from_island_chat(self) -> None:
        """岛对话气泡里的「显示桌宠」：恢复全部窗并同步岛状态。

        overlay 拓扑下 ``instances[].win`` 恒为 None（PetWindow 不构造），旧的全窗
        ``show()`` 循环一只都命不中——宠物实际没恢复，岛却被直接标成可见。恢复面是
        唯一合成窗，那里走壳自己的 ``set_pet_visible(True)``（``overlay.show()`` +
        共享子系统 resume + 岛状态同步，与岛单击 toggle 同一条路，见
        ``overlay_shell.py`` 的 ``set_pet_visible``）。

        整窗恢复之后还要补一手**逐只恢复**（见 :meth:`_restore_overlay_sprites`）：
        壳的逐只隐藏是用户显式决定（托盘「显示这只」），``set_pet_visible(True)``
        按设计保留它——只走整窗恢复的话，逐只全藏过的桌宠"窗在、宠不在"，岛却被
        标成可见。
        """
        overlay = getattr(self, "_overlay_shell", None)
        if overlay is not None:
            overlay.set_pet_visible(True)
            self._restore_overlay_sprites(overlay)
        else:
            for inst in getattr(self, "_instances", []):
                win = getattr(inst, "win", None)
                if win is not None:
                    win.show()
        island = getattr(self, "island", None)
        if island is not None and shiboken6.isValid(island):
            island.set_pet_visible(True)
        bubble = getattr(self, "island_chat", None)
        if bubble is not None and shiboken6.isValid(bubble):
            bubble.close()

    def _restore_overlay_sprites(self, shell) -> None:
        """整窗恢复后仍无可见 sprite 时，把逐只被藏的 sprite 全部恢复可见。

        ``OverlayShell.set_pet_visible`` 的语义是「整窗显隐保留逐只选择」（壳的
        ``_set_all_clips_paused`` 恢复侧显式跳过 ``visible`` 为假的 sprite，托盘
        「显示这只」的隐藏不会被整窗显示悄悄解除）——那条语义是对的，问题在于岛
        气泡「显示桌宠」是**唯一**能把隐藏的桌宠叫回来的手柄（桌宠不可见时用户够
        不着托盘逐只菜单），它不能只恢复窗。

        只在**一只可见的都没有**时才补这一步：有可见 sprite 就一根逐只标志都不碰，
        用户的逐只选择原样保留；没有 sprite 清单的精简装配/测试替身按「窗口可见即
        可见」处理（与 :meth:`_overlay_sprite_visible` 同一口径），什么都不做。

        遍历**全部宿主**：混合缩放下每块屏一张窗，只恢复主窗的话副屏那几只藏着的
        宠永远叫不回来（用户点了「显示桌宠」却仍有屏是空的）。
        """
        if self._overlay_sprite_visible(shell):
            return
        setter = getattr(shell, "set_sprite_visible", None)
        if not callable(setter):
            return
        for host in self._overlay_hosts(shell):
            sprites = getattr(getattr(host, "overlay", None), "sprites", None)
            if sprites is None:
                continue  # 该宿主没有 sprite 清单接口（精简装配）
            for sprite in list(sprites):
                setter(sprite, True)

    def _open_settings_from_island(self) -> None:
        inst = self.instance
        if inst is not None and callable(getattr(inst, "open_modern_settings", None)):
            inst.open_modern_settings()

    def _on_global_chat_finished(self, text: str) -> None:
        """ChatService 全局完成回调（GUI 线程）：驱动灵动岛事件动效。"""
        island = getattr(self, "island", None)
        if island is None or not shiboken6.isValid(island):
            return
        island.set_last_message(text)
        island.notify_event("reply")
        # 桌宠隐藏时岛是唯一常驻交互面：回复到达在岛上弹预览气泡（不抢焦点，
        # 超时自动收回）。岛气泡在场时跳过——多半是气泡自己刚回完话，别再弹。
        if not self._aggregate_pet_visible() and self._island_chat_available():
            bubble = getattr(self, "island_chat", None)
            if bubble is not None and shiboken6.isValid(bubble) and bubble.isVisible():
                return
            # 完整聊天窗已打开：回复已有去处，不再弹岛预览
            for inst in getattr(self, "_instances", []):
                for _attr in ("legacy_chat_window", "modern_chat_window"):
                    _w = getattr(inst, _attr, None)
                    if _w is not None and shiboken6.isValid(_w) and _w.isVisible():
                        return
            self._show_island_chat(activate=False, reply_text=text)

    def _aggregate_pet_visible(self) -> bool:
        """是否有任一窗可见（聚合可见态——灵动岛按它同步 set_pet_visible）。"""
        overlay = getattr(self, "_overlay_shell", None)
        if overlay is not None:
            # overlay 拓扑：instances[].win 恒为 None（PetWindow 不构造），
            # 聚合可见态由合成窗承担。但合成窗是**常驻**透明全屏窗——逐只显隐
            # （OverlayShell.set_sprite_visible）只翻 sprite.visible、不关它，故
            # 窗口可见只是必要条件：还要至少剩一只可见 sprite（见下）。混合缩放
            # 下一块屏一张窗（多宿主）：任一张窗可见 + 任一宿主上有一只可见即成立，
            # 只看主窗会把"主窗空着、副屏还有宠"判成隐藏。
            windows = [getattr(host, "overlay", None)
                       for host in self._overlay_hosts(overlay)]
            if not any(self._window_visible(window)
                       for window in windows if window is not None):
                return False
            return self._overlay_sprite_visible(overlay)
        return any(
            inst.win is not None and getattr(inst.win, "isVisible", lambda: True)()
            for inst in self._instances
        )

    @staticmethod
    def _overlay_hosts(shell) -> list:
        """壳的全部宿主（混合缩放下每块屏一个）；没有 ``hosts()`` 的精简装配按单宿主算。"""
        getter = getattr(shell, "hosts", None)
        return list(getter()) if callable(getter) else [shell]

    @staticmethod
    def _window_visible(window) -> bool:
        """该窗口是否在屏上；原生窗口已销毁（RuntimeError）算不可见。"""
        try:
            return bool(window.isVisible())
        except RuntimeError:
            return False

    @staticmethod
    def _overlay_sprite_visible(shell) -> bool:
        """是否至少有一只可见 sprite（主宠与子宠、**每块屏的窗**同等计入）。

        逐只藏光全部 sprite 后窗口仍 ``isVisible()``，此时桌宠在屏幕上其实一只
        都没有——聚合若判可见，岛会把快速对话锚到不可见的 sprite，隐藏期该弹的
        岛气泡也不再弹。可见性读壳自己的 ``_sprite_visible``（同一判据，含鸭式
        sprite 的兜底）。

        遍历全部宿主：混合缩放下每块屏一张窗，只看主窗（``shell.overlay``）会把
        "全迁到副屏"判成一只都没有、把"主窗藏光但副屏有宠"判成隐藏。两种兜底要
        分开：**没有** sprite 清单这个接口（精简装配/测试替身）→ 按「窗口可见即
        可见」；清单**存在**但一只都没有 → 真的没有可见宠。
        """
        probe = getattr(shell, "_sprite_visible", None)
        has_list = False
        for host in AppShell._overlay_hosts(shell):
            sprites = getattr(getattr(host, "overlay", None), "sprites", None)
            if sprites is None:
                continue  # 这个宿主没有 sprite 清单接口
            has_list = True
            for sprite in list(sprites):
                visible = (probe(sprite) if callable(probe)
                           else getattr(sprite, "visible", True))
                if visible:
                    return True
        return not has_list

    def _toggle_pet_from_island(self) -> None:
        # overlay 拓扑：inst.win 全为 None，旧循环拿不到任何窗——路由到
        # OverlayShell（其 set_pet_visible 自带岛状态同步与共享子系统
        # pause/resume，语义 = 下方 legacy 全窗 toggle 的单窗形态）。
        overlay = getattr(self, "_overlay_shell", None)
        if overlay is not None:
            overlay._toggle_pet_visible()
            return
        # 批5.2a §③.4：灵动岛单击 toggle **全部**窗（任一可见 → 全部隐藏；否则全部显示），
        # 并按聚合可见态同步 set_pet_visible（替代 spike 只 toggle 主窗的 P2-5 缺口）。
        wins = [inst.win for inst in self._instances if inst.win is not None]
        if not wins:
            return
        any_visible = any(getattr(w, "isVisible", lambda: True)() for w in wins)
        if any_visible:
            for w in wins:
                w.hide(notify=False)
        else:
            for w in wins:
                w.show()
        if getattr(self, "island", None) is not None:
            self.island.set_pet_visible(not any_visible)

    def _apply_balance_timer(self) -> None:
        self._balance_timer.stop()
        if not feature_activation_mod.is_active("dynamic_island"):
            # 余额是灵动岛模块的子能力（清单 §余额：自动刷新/峰谷文案/岛信息槽
            # 同属一模块）：模块停用后本进程一次都不许自己发余额请求。
            return
        minutes = max(0, int(self.config.get("balance_refresh_minutes", 0) or 0))
        if minutes:
            self._balance_timer.start(minutes * 60000)

    def _island_icon_pixmap(self):
        """灵动岛"鱼本体头像"：取首个桌宠窗的当前帧图标；无窗/无帧返回 None（岛侧会重试）。

        4.4a overlay 拓扑：``instances[].win`` 恒为 None（单合成窗，桌宠在
        OverlayShell 里），图标改从壳的主 sprite 当前帧取（``OverlayShell.icon_pixmap``
        与旧 ``PetWindow.icon_pixmap`` 同款裁剪/缩放）；两处都取不到才返回 None，
        岛侧按 provider 契约（dynamic_island._icon_pixmap 不缓存 None）稍后重试。
        """
        for inst in getattr(self, "_instances", []):
            win = getattr(inst, "win", None)
            if win is not None:
                pm = win.icon_pixmap(64)
                if pm is not None and not pm.isNull():
                    return pm
        overlay_shell = getattr(self, "_overlay_shell", None)
        if overlay_shell is not None:
            pm = overlay_shell.icon_pixmap(64)
            if pm is not None and not pm.isNull():
                return pm
        return None

    def _island_tier_hint(self) -> str:
        """灵动岛余额峰谷提示文案（与 _update_island_balance / 静默查询共用）。

        pet.island.balance 按需导入：本函数只在"岛卡片展开 / 余额查询返回"时才被调用，
        启动路径（_sync_dynamic_island）不碰它（见 _show_balance_payload）。
        """
        from .island import balance as balance_mod

        peak_label, idle_label = balance_mod.resolve_tier_labels(
            str(self.config.get("balance_tier_labels_mode", "default") or "default"),
            str(self.config.get("balance_tier_label_peak", "") or ""),
            str(self.config.get("balance_tier_label_idle", "") or ""),
        )
        return balance_mod.deepseek_pricing_hint(
            peak_label=peak_label, idle_label=idle_label,
        )

    def _update_island_balance(self, payload, *, animate: bool = True) -> None:
        """把余额文本/峰谷提示同步给灵动岛（若有）。

        animate=False 用于静默刷新路径（卡片展开后的缓存命中/后台查询）：
        只更新文本不播余额弹跳动画——弹跳会把展开卡片顶出窗口截边。
        """
        if getattr(self, "island", None) is None:
            return
        text = "余额 --"
        if isinstance(payload, dict):
            text = str(payload.get("text") or "余额 --")
        self.island.set_balance_info(self._island_tier_hint(), text)
        if animate:
            self.island.notify_event("balance")

    def _quiet_balance_refresh(self, *, force: bool = False) -> None:
        """灵动岛卡片展开时的静默余额刷新：不冒泡、不播动画，只更新岛卡片。

        30s 内存/文件缓存命中直接用；否则后台查询（60s 节流，查询忙时跳过）。
        未配置 API Key 时明确提示，不再停留 "余额 --"。
        """
        island = getattr(self, "island", None)
        if island is None or getattr(self, "_quiet_balance_busy", False):
            return
        if not feature_activation_mod.is_active("dynamic_island"):
            # 余额属灵动岛模块：模块停用后卡片不可能展开（岛根本不存在），
            # 这条直达回调也不许绕过模块闸去发网络请求。
            return
        if not getattr(self, "enable_chat", True):
            island.set_balance_info(self._island_tier_hint(), "此版本无余额查询")
            return
        now = time.monotonic()
        import hashlib
        settings = self.config.chat_settings()
        provider = settings.active_config
        provider.api_key = self.config.resolve_api_key(provider)
        if not provider.api_key:
            island.set_balance_info(
                self._island_tier_hint(), "未配置 API Key（设置 → 聊天）")
            return
        key_digest = hashlib.sha256(str(provider.api_key or '').encode()).hexdigest()[:12]
        provider_key = '|'.join([
            str(getattr(provider, 'id', '') or ''),
            str(provider.base_url or ''),
            key_digest,
        ])
        if self._balance_cache is not None and now - self._balance_cache[0] < 30.0 \
                and self._balance_cache[2] == provider_key:
            self._update_island_balance(self._balance_cache[1], animate=False)
            return
        file_payload = self._read_balance_file_cache(provider_key)
        if file_payload is not None:
            self._balance_cache = (now, file_payload, provider_key)
            self._update_island_balance(file_payload, animate=False)
            return
        last = getattr(self, "_quiet_balance_last", None)
        if not force and last is not None and now - last < 60.0:
            # 节流命中且缓存已过期：明示状态，不停留在旧值上装死
            island.set_balance_info(self._island_tier_hint(), "刚刚查询过，稍后自动更新")
            return
        self._quiet_balance_last = now
        island.set_balance_info(self._island_tier_hint(), "查询中…")
        bridge = _QuietBalanceBridge(owner=self)
        self._quiet_balance_bridge = bridge
        self._quiet_balance_busy = True  # 独立忙标志：不占显式查询的闸门
        try:
            threading.Thread(
                target=self._balance_worker,
                args=(bridge, provider.base_url, provider.api_key, provider.verify_ssl, provider_key),
                kwargs={"quiet": True},
                daemon=True, name='pet-balance-quiet',
            ).start()
        except Exception as exc:  # noqa: BLE001 - 启动失败也必须释放忙状态
            self._quiet_balance_busy = False
            QTimer.singleShot(0, lambda message=str(exc): bridge.done.emit(False, message))

    # ------------------------------------------------------------ 余额
    def show_balance(self, parent=None) -> None:
        if not feature_activation_mod.is_active("dynamic_island"):
            # 余额归属灵动岛模块（features.py 的 config_keys）：模块未激活时
            # 托盘/右键/点击三条入口都不该走到这里，这是绕过菜单投影的兜底——
            # 不查余额、不起 pet-balance 线程、不碰余额缓存文件。
            logging.info("灵动岛模块未激活，忽略「DeepSeek 余额」请求")
            return
        # 宿主判定：必须是能冒泡的 pet 形宿主。overlay 菜单把合成窗
        # （OverlayWindow，无 show_bubble）当 parent 传下来，直接用它会在
        # _show_balance_payload 里 AttributeError；overlay 拓扑下 ``instance.win``
        # 又恒为 None——两条都收敛到 ``self.win``（原生 Win 或 sprite 壳）。
        win = parent
        if win is None or not callable(getattr(win, "show_bubble", None)):
            win = self.win
        if win is None or self._balance_busy or not win.isVisible():
            return
        now = time.monotonic()
        # 余额缓存绑定 provider 身份（id + base_url + key 摘要）：同地址不同账号也不串号；
        # 摘要不可逆推原 key，不落敏感信息。
        import hashlib
        settings = self.config.chat_settings()
        provider = settings.active_config
        provider.api_key = self.config.resolve_api_key(provider)
        key_digest = hashlib.sha256(str(provider.api_key or '').encode()).hexdigest()[:12]
        provider_key = '|'.join([
            str(getattr(provider, 'id', '') or ''),
            str(provider.base_url or ''),
            key_digest,
        ])
        if self._balance_cache is not None and now - self._balance_cache[0] < 30.0 \
                and self._balance_cache[2] == provider_key:
            self._update_island_balance(self._balance_cache[1])
            _show_balance_payload(win, self._balance_cache[1])
            return
        file_payload = self._read_balance_file_cache(provider_key)
        if file_payload is not None:
            self._balance_cache = (now, file_payload, provider_key)
            self._update_island_balance(file_payload)
            _show_balance_payload(win, file_payload)
            return
        self._balance_busy = True
        # 延迟到事件循环空闲再冒泡：macOS 菜单跟踪会话内新建/显示窗口会被
        # AppKit 抑制（与设置对话框首次点击无反应同源），singleShot 在 macOS
        # 上要等菜单关闭后才派发，Windows 上立即派发也无害。
        QTimer.singleShot(0, lambda: win.show_bubble('让我看看余额…', duration_ms=6000))
        bridge = _BalanceBridge(win, owner=self)
        self._balance_bridge = bridge
        try:
            threading.Thread(
                target=self._balance_worker,
                args=(bridge, provider.base_url, provider.api_key, provider.verify_ssl, provider_key),
                daemon=True, name='pet-balance',
            ).start()
        except Exception as exc:  # noqa: BLE001 - 启动失败也必须释放忙状态
            self._balance_busy = False
            error_message = f'余额查询失败：{exc}'
            QTimer.singleShot(0, lambda message=error_message: bridge.done.emit(False, message))

    def show_weather(self, parent=None) -> None:
        """「查看天气」：后台取当前城市天气并在桌宠气泡报出。

        与 ``show_balance`` 同口径：``parent`` 是气泡宿主（旧版拓扑是 PetWindow、
        overlay 拓扑是壳）；服务懒创建、进程级单例（多窗共用）。
        """
        if not feature_activation_mod.is_active("weather"):
            # 模块未激活：菜单层已投影过滤，这里是绕过菜单直达回调的兜底
            # （旧布局残留/外部触发），不建服务、不起网络线程。
            logging.info("天气模块未激活，忽略「查看天气」请求")
            return
        from .weather.service import WeatherService

        if self._weather_service is None:
            self._weather_service = WeatherService(self)
        self._weather_service.show_weather(parent)

    def _balance_worker(self, bridge, base_url: str, api_key: str, verify_ssl: bool, provider_key: str = '', *, quiet: bool = False) -> None:
        from .island import balance as balance_mod  # 按需导入（见 _show_balance_payload）
        try:
            info = balance_mod.fetch_balance(base_url, api_key, verify_ssl=verify_ssl)
            text = balance_mod.format_balance(info)
            payload = {"text": text, "info": info}
            self._balance_cache = (time.monotonic(), payload, provider_key)
            self._write_balance_file_cache(payload, provider_key)
            bridge.done.emit(True, payload)
        except Exception as exc:  # noqa: BLE001 - 任何失败走气泡提示
            bridge.done.emit(False, f'余额查询失败：{exc}')
        finally:
            if quiet:
                self._quiet_balance_busy = False
            else:
                self._balance_busy = False

    def _read_balance_file_cache(self, provider_key: str = '') -> dict | None:
        """读取跨实例共享的余额缓存（30s 内有效，且必须是同一 provider 的缓存）。

        返回 {"text": ..., "info": {...}}；兼容旧版只存 text 字符串的缓存。
        """
        try:
            data = json.loads(self._balance_cache_path.read_text(encoding='utf-8'))
            if not isinstance(data, dict):
                return None
            if str(data.get('provider', '') or '') != provider_key:
                return None
            if time.time() - float(data.get('ts', 0) or 0) >= 30.0:
                return None
            text = str(data.get('text', '') or '')
            if not text:
                return None
            info = data.get('info')
            return {
                'text': text,
                'info': info if isinstance(info, dict) else {},
            }
        except (OSError, ValueError, TypeError):
            pass
        return None

    def _write_balance_file_cache(self, payload: dict, provider_key: str = '') -> None:
        """写入跨实例共享的余额缓存（原子替换，绑定 provider）。

        同时保存 text 和 info，使缓存命中时也能显示峰谷副标题并播放余额动画。
        """
        try:
            self._balance_cache_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._balance_cache_path.with_suffix(f'.{os.getpid()}.tmp')
            tmp.write_text(
                json.dumps({
                    'ts': time.time(),
                    'text': str(payload.get('text') or ''),
                    'info': payload.get('info') or {},
                    'provider': provider_key,
                }, ensure_ascii=False),
                encoding='utf-8',
            )
            tmp.replace(self._balance_cache_path)
        except OSError:
            pass

    # ------------------------------------------------------------ 更新
    def check_update(self, parent=None) -> None:
        # 重入防护（审查 GLM-L3）：连点不应起多个检查线程/叠气泡
        if getattr(self, "_update_checking", False):
            return
        self._update_checking = True
        target = parent or (self.instance.win if self.instance is not None else None)
        if target is not None:
            target.show_bubble("正在检查更新…", duration_ms=6000)
        bridge = _UpdateBridge(target, owner=self)
        self._update_bridge = bridge
        # 完成后放行下一次检查（无论成败）
        bridge.done.connect(lambda *_: setattr(self, "_update_checking", False))

        def worker() -> None:
            try:
                release = updater.latest_release()
            except Exception as exc:
                # 后台线程异常必须收口回 GUI，否则更新提示永远停在
                # 「正在检查更新」（审查 P1-01）
                logging.debug("检查更新失败", exc_info=True)
                bridge.done.emit(False, str(exc))  # 前缀由 _UpdateBridge._show 统一加
                return
            bridge.done.emit(bool(release), release or "无法连接更新服务，请稍后重试。")

        threading.Thread(target=worker, daemon=True, name="pet-update-check").start()

    # ------------------------------------------------------------ 生小肥鱼 / 多窗
    def spawn_pet(self) -> None:
        """生小肥鱼：统一走进程内新窗，不再拉起独立桌宠进程。

        4.4b：多进程多宠退役层删除后，多宠的唯一形态是进程内多窗/多 sprite。
        ``--slot`` / ``DSH_PET_SPAWN_OFFSET_INDEX`` 兼容解析保留（T5），
        落点错峰仍按 spawn 序号计算。

        T4 Phase A：捕获模式（``stream_capture_mode``）重启后走 legacy **单窗**
        形态，多宠已收拢为主宠物；此时再手工生一只，在用户看来就是"又冒出一只
        完整桌宠"，与单窗口径冲突（且 OBS 源列表会出现两个同标题窗口）。显式
        ``PET_RENDER_TOPOLOGY=legacy``（dev 逃生门）同拓扑同语义，一并拒绝。

        slot0 档案**不可用**（``target-corrupt`` 降级）时这条"恢复 slot0"也要拒绝
        （2026-10-10 ops D2-新）：旧链会 ``begin_slot_generation`` → 用**默认视图**
        建一只看着正常的宠 → 把 0 记回活跃集合，而档案层所有写都是 ``refused-corrupt``
        （坏文件确实没被覆盖）。用户拿到一只永远存不住设置的宠，且全程零提示。
        两个拓扑对同一次事故必须同源处置（overlay 侧本来就会失败），故这里改为
        **同样失败 + 说清路径**，坏文件仍一个字节不动。
        """
        from .overlay_shell import is_overlay_topology
        if not is_overlay_topology():
            if 0 not in self._active_pet_slots:
                if getattr(self, "_slot0_profile_unusable", False):
                    logging.warning("slot0 恢复被拒（档案不可用）：%s",
                                    self.slot0_profile_diagnosis())
                    self._notify_slot0_profile_unusable("恢复 slot0")
                    return
                from . import pet_profiles

                generation = pet_profiles.begin_slot_generation(self.config.dir, 0)
                if not generation.ok:
                    logging.warning("slot0 恢复失败: %s", generation)
                    return
                self.instance.config.reload()
                self._create_ui_with_character_fallback(str(self.instance.config.get(
                    "character", catalog.DEFAULT_CHARACTER)))
                self._active_pet_slots.append(0)
                self._save_active_set(action="恢复 slot0")
                self._refresh_tray_menu()
                return
            logging.info("直播捕获兼容（单窗形态）下不生小肥鱼：仅主宠物")
            win = getattr(getattr(self, "instance", None), "win", None)
            if win is not None:
                win.show_bubble("直播捕获兼容模式仅主宠物", duration_ms=4000)
            return
        overlay = getattr(self, "_overlay_shell", None)
        if overlay is not None:
            overlay.spawn_pet()
            return
        try:
            self._spawned_pet_count += 1
            self.spawn_in_process_window(self._spawned_pet_count)
        except Exception as exc:
            self._spawned_pet_count = max(0, self._spawned_pet_count - 1)
            logging.exception('进程内生成小肥鱼失败')
            _show_startup_error('生小肥鱼失败', str(exc))

    def clear_spawned_pets(self) -> None:
        """右键菜单快捷入口：一键静默退出所有小肥鱼（设置与数据保留）。

        批 I：按用户要求去掉确认框与结果框——操作本身不删数据、子肥鱼可
        随时重新生成，无需确认；子肥鱼消失本身就是反馈，结果写日志。
        4.4a：跨进程文件级清理（``child_pet_cleanup``：runtime 标记 + taskkill）
        停用——退役层删除后不存在跨进程子宠，进程内链式关闭即全部。
        关闭走 QTimer.singleShot(0) 逐只链式执行（每只之间让出事件循环），
        每窗的重资源回收（writer 关闭/agent shutdown）挪到后台 reaper 线程
        （批 G，_on_window_exit_requested 的 defer_heavy_teardown 路径），
         UI 线程只留关窗/摘标记等毫秒级必做步骤。
        """
        if self._clear_spawned_pending:
            # 链式关闭进行中：重复点击直接忽略（同一任务会清干净，保持幂等）。
            return
        refs = [weakref.ref(inst) for inst in self._instances
                if inst is not self.instance]
        if not refs:
            return
        # 设置页闸门**整批前置**（2026-10-11 ops P3-c）：任一目标窗的设置页里用户选了
        # 「继续编辑」，就整次取消、一只都不退（中途才发现的话，前面的宠已经退出去了，
        # "取消"就名不副实）。没有被拒时所有设置页都已经过完闸门，链里再逐只关都是走
        # 既有路径。
        for target in [ref() for ref in refs]:
            if target is not None and not self._settings_window_releasable(target):
                logging.info("一键清除：用户留在设置页里继续改，本次取消")
                return
        self._clear_spawned_pending = True
        QTimer.singleShot(0, lambda: self._clear_spawned_chain(refs, 0))

    def _clear_spawned_chain(self, refs, index: int) -> None:
        """逐只异步关闭进程内子窗（每只之间让出事件循环，UI 不冻结）。

        窗口已销毁（弱引用失效）或已被关闭（不在登记表）则跳过；链尾复位
        进行中标记。异常只记录不中断，保证标记一定复位。
        """
        if index >= len(refs):
            self._clear_spawned_pending = False
            return
        inst = refs[index]()
        if inst is not None and inst in self._instances:
            try:
                # 批 G：链式路径重资源回收后台化（writer/agent 的有界 join
                # 移出 UI 线程），每窗 UI 线程单步阻塞压到毫秒级。
                self._on_window_exit_requested(inst, defer_heavy_teardown=True)
            except Exception:
                logging.exception(
                    "清除子肥鱼：关闭进程内小肥鱼失败 (slot=%s)",
                    getattr(inst, "slot_id", None))
        QTimer.singleShot(
            0, lambda: self._clear_spawned_chain(refs, index + 1))

    def spawn_in_process_window(self, offset_index: int = 1) -> PetInstance:
        """进程内创建第二个 PetInstance（不共享库/Config/SessionStore）。

        - 新 slot 身份：4.4a 起走 **D6 无锁分配器**（``overlay_spawn_state.
          allocate_slot``，与 overlay 拓扑同一套口径），不再抢 slot-N.lock
          ——多进程多宠退役层的文件锁不再被任何路径使用；
        - 独立 Config(instance_id=slot-N)，显式传 instance_id（不再依赖进程级
          DSH_PET_INSTANCE），独立 MovieLibrary / PetWindow / SessionStore 目录；
        - 碰撞不再经 QLocal 回环：多宠碰撞归 overlay 拓扑的 sprite 世界；
          legacy 的多窗是渲染面（捕获模式收窄），窗间不做跨进程冲量交换。
        """
        overlay = getattr(self, "_overlay_shell", None)
        if overlay is not None:
            overlay.spawn_pet()
            return self.instance
        from .overlay_shell import is_overlay_topology

        if not is_overlay_topology():
            raise RuntimeError("捕获模式仅允许恢复slot0，不创建额外多宠窗口")
        config_dir = self.config.dir
        from . import overlay_spawn_state as overlay_spawn_state_mod

        # 进程内 slot 语义 = 窗身份分配：已占用 = 目录内既有 config-slot-N.json
        # 身份 ∪ 本进程活跃窗身份；分配单调增长，永不与保留身份相撞。
        used = {inst.slot_id for inst in self._instances if inst.slot_id is not None}
        slot_id = overlay_spawn_state_mod.allocate_slot(config_dir, active_slots=used)
        instance_id = slot_manager_mod.slot_to_instance_id(slot_id)
        # 新 slot 落种：走共享落种函数。无存档 slot 按主设置落种；存在但未在该
        # 子肥鱼设置界面自定义过的 slot 按主设置刷新；已自定义（user_customized）
        # 的 slot 一个键都不碰。落种/刷新永不写位置键。
        from . import pet_profiles
        from .settings_slot_config import route_settings_config

        if not pet_profiles.slot_profile_exists(config_dir, slot_id):
            from .config import normalize_pet_profile_snapshot

            source = normalize_pet_profile_snapshot(pet_profiles.read_slot_profile(config_dir, 0).data)
            seeded = pet_profiles.write_profile_settings(config_dir, slot_id,
                {key: source[key] for key in pet_profiles.PER_PET_SETTING_KEYS})
            if not seeded.ok:
                raise RuntimeError(str(seeded))
        # 复用主窗同一配置根目录（AppShell.config.dir 的父目录），使所有窗的
        # config-slot-N.json / sessions-slot-N 落在同一 APP_DIR_NAME 下，仅按
        # instance_id 区分；显式传 instance_id，不再依赖进程级 DSH_PET_INSTANCE。
        new_config = route_settings_config(Config(base=self.config.dir.parent, instance_id=instance_id))
        character_id = str(new_config.get('character', catalog.DEFAULT_CHARACTER))
        inst = PetInstance(
            self, new_config, enable_chat=self.enable_chat,
            slot_id=slot_id, spawn_offset=offset_index,
        )
        # 批5.3：P1-6 移除——进程内多窗不再停用任何窗的共享解码；新窗与主窗
        # 共用同一进程级 DecodeFanoutHub（同素材首窗发布、同速窗进食）。
        # build_tray=False：非主窗不再新建/替换进程级托盘，改由 _refresh_tray_menu 聚合。
        inst._build_window(character_id, build_tray=False)
        self._instances.append(inst)
        # 硬墙钩子只在碰撞体 start 时挂过一轮：新窗补挂，否则新鱼会穿过岛。
        island_body = getattr(self, "island_collision", None)
        if island_body is not None:
            island_body.refresh_hooks()
        inst._apply_spawn_offset()
        self._refresh_tray_menu()
        # 批5.2a §③.4：_check_autostart_wanted 逐窗（读各自 config），新窗入列后补一次。
        QTimer.singleShot(3500, inst._check_autostart_wanted)
        # N-5：msg 不手写 [slot-N] 前缀——_slot_wrap/_SlotLogFilter 会加调用方
        # 槽位前缀，叠加成双前缀纯噪音；新窗身份保留在正文里。
        logging.info("进程内新窗已创建 (slot=%s, instance=%s)", slot_id, instance_id)
        return inst

    def _on_window_exit_requested(self, instance: PetInstance,
                                  *, defer_heavy_teardown: bool = False) -> None:
        """窗级「退出这只」（R5 切分）：只收口本窗，不碰其它窗的进程级资源。

        顺序：存本窗位置 → 停本窗预热/Agent → 保存本窗三聊天窗 live session
        （P0-2，对齐 aboutToQuit 安全网）→ 关闭/断开本窗从属窗（P1-5，防 writer
        复活）→ 删本窗 runtime 标记 → 关本窗 sessions writer（非 permanent，
        2s 超时）→ 释放本窗 slot 锁 → 停本窗碰撞会话（P1-1；共享解码 hub 为
        进程级，其 shutdown 是 no-op，不在此停）→ 关本窗 →
        子窗从呈现集合移除；slot0关闭只释放呈现，固定控制instance与共享会话保留。
        不提升、不quit，进程级writer仅在显式退出应用收口。

        ``defer_heavy_teardown=True``（批 G，仅「退出子肥鱼」链式路径使用）：
        把有界但秒级的重资源回收——本窗 sessions writer 关闭（最多 2s join）、
        agent_link shutdown（最多 2s 共享 join）、碰撞会话停止（最多 3s+1s
        wait）——挪到进程级后台 reaper 线程串行执行；UI 线程只保留关窗、
        摘 runtime 标记、释放 slot 锁、从登记表移除等毫秒级必做步骤，
        N 只连清时主桌宠不再冻结。默认 False = 「退出这只」单窗路径保持
        既有同步语义逐位不变。重回收只做线程 join / queued 调用 / 纯 Python
        注册表操作，不触碰 Qt 对象，后台执行安全。

        **第一步是设置页闸门**（2026-10-11 ops P3-c）：进程内设置页开着时先过一遍
        它的关闭闸门（保存/放弃/继续编辑）。用户选「继续编辑」→ **整次收口取消**，
        位置、预热、Agent、会话、引用、活跃集合一个都不动；否则才往下走。放在这里
        而不是收口中途，是因为中途 return 只会留下半死的宠物（见
        ``_settings_window_releasable``）。
        """
        if not self._settings_window_releasable(instance):
            logging.info("退出这只：用户留在设置页里继续改，本次收口取消（一个资源都没动）")
            return
        win = instance.win
        if instance is self.instance:
            if win is None:
                return
            win.save_position()
            win.agent_link_manager = None
            win.proactive_watcher = None
            win.close()
            instance.win = None
            self._active_pet_slots = [n for n in self._active_pet_slots if n != 0]
            self._save_active_set(action="关闭 slot0")
            self._build_empty_pet_tray()
            return
        heavy_jobs: list[tuple[str, object]] = []  # defer 模式的重回收任务
        if win is not None:
            try:
                win.save_position()
            except Exception:
                logging.exception("退出这只：保存位置失败")
            try:
                if getattr(win, 'lib', None) is not None:
                    win.lib.pause_warm()
            except Exception:
                logging.exception("退出这只：暂停预热失败")
            agent_mgr = getattr(win, 'agent_link_manager', None)
            if agent_mgr is not None:
                if defer_heavy_teardown:
                    # 摘下引用再关窗：closeEvent 也会调 shutdown()，不在 UI
                    # 线程重复 join（reaper 统一收口，shutdown 幂等）。
                    win.agent_link_manager = None
                    heavy_jobs.append(("关闭 Agent", agent_mgr.shutdown))
                else:
                    try:
                        agent_mgr.shutdown()
                    except Exception:
                        logging.exception("退出这只：关闭 Agent 失败")
        # 批5.2 P0-2：先保存本窗三聊天窗的 live session，再关写盘 writer
        #（对齐 aboutToQuit 安全网：退出该窗不丢内存态会话）。
        for _w in (instance.legacy_chat_window, instance.modern_chat_window, instance.quick_chat):
            _session = getattr(_w, 'session', None)
            _store = getattr(_w, 'store', None)
            if _session is not None and _store is not None:
                try:
                    _store.save(_session)
                except Exception:
                    logging.exception("退出这只：保存本窗会话失败")
        # 批5.2 P1-5：关闭/隐藏本窗的聊天窗与设置窗并断开引用，防止孤儿顶层窗
        # 在 writer 关闭后经 store 提交、复活写盘 worker（常驻到进程结束）。
        self._close_instance_subwindows(instance)
        if win is not None:
            marker_remover = getattr(win, 'remove_runtime_marker', None)
            if callable(marker_remover):
                try:
                    marker_remover()
                except Exception:
                    logging.exception("退出这只：删除 runtime 标记失败")
        # 批5.2 P1-7：运行期关窗不许冻 GUI 10s——只关本窗 writer，timeout 降到 2s。
        # 批 G：defer 模式下这 2s 有界 join 也挪到 reaper 线程（会话保存已在
        # 上方同步完成，顺序由 reaper 串行保证）。
        if defer_heavy_teardown:
            heavy_jobs.append(
                ("关闭会话写盘 worker",
                 lambda: self._close_instance_session_writer(instance)))
        else:
            self._close_instance_session_writer(instance)
        # 4.4a：本窗不再持有 slot 文件锁与碰撞 IPC 会话（退役层停用）。
        try:
            instance.broker_facade.shutdown()
        except Exception:
            logging.exception("退出这只：关闭 broker 失败")
        try:
            if win is not None:
                win.close()
        except Exception:
            logging.exception("退出这只：关闭窗口失败")
        if instance in self._instances:
            self._instances.remove(instance)
        self._refresh_tray_menu()
        if heavy_jobs:
            self._enqueue_heavy_teardown(instance, heavy_jobs)

    def _teardown_reaper_queue(self) -> "queue.Queue":
        """懒创建的进程级重资源回收队列（批 G）：单条 daemon 线程串行执行
        各窗退出时的 writer 关闭 / agent shutdown / 碰撞停止（都是有界但
        秒级的线程 join/wait），UI 线程因此不被阻塞。daemon：进程退出不强等
        （aboutToQuit 的进程级收口另有兜底），队列任务失败只记录不中断。"""
        q = getattr(self, "_teardown_queue", None)
        if q is None:
            q = queue.Queue()

            def reap() -> None:
                while True:
                    jobs = q.get()
                    for label, fn in jobs:
                        try:
                            fn()
                        except Exception:
                            logging.exception("退出子肥鱼：后台重回收失败 (%s)", label)

            threading.Thread(
                target=reap, daemon=True, name="pet-teardown-reaper").start()
            self._teardown_queue = q
        return q

    def _enqueue_heavy_teardown(self, instance: PetInstance, jobs: list) -> None:
        """把一窗的重回收任务排进 reaper（幂等：同一实例只排一次）。"""
        if getattr(instance, "_heavy_teardown_enqueued", False):
            return
        instance._heavy_teardown_enqueued = True
        self._teardown_reaper_queue().put(jobs)

    def _settings_window_releasable(self, instance: PetInstance) -> bool:
        """不可逆收口**之前**：把这张窗的进程内设置页过一遍关闭闸门。

        True = 可以继续收口（没有设置页，或它已经关掉）；False = 用户在失败框里选了
        「继续编辑」，调用方必须**整次取消**这次关闭请求，一个资源都不许动。

        为什么必须在收口之前做（ops P3-c）：设置页的关闭闸门在保存失败时会弹框，用户选
        「继续编辑」时 ``close()`` 被 ignore。等收口流程走到 ``_close_instance_subwindows``
        才发现就晚了——那时位置已存、预热已停、Agent 已关、会话写盘 worker 已关，父窗随即
        销毁，草稿窗成孤儿跟着消失（那里也**没法**在中途安全退出：留下的是半死的宠物）。

        只认 ``modern_settings_dialog``（有保存闸门的那一个）；ChatSettingsDialog 的
        closeEvent 没有闸门，仍由 ``_close_instance_subwindows`` 收。
        """
        dialog = getattr(instance, "modern_settings_dialog", None)
        if dialog is None:
            return True
        try:
            closed = dialog.close()
        except Exception:
            # C++ 对象已经没了（WA_DeleteOnClose 之后又被回收）：没有草稿可保护。
            logging.exception("退出前关闭进程内设置页失败")
            return True
        if isinstance(closed, bool):
            return closed
        # 老替身/桩：close() 没有返回值，退回可见性判据（隐藏的窗口不可能有用户草稿）。
        visible = getattr(dialog, "isVisible", None)
        return not (callable(visible) and bool(visible()))

    def _release_settings_windows(self) -> bool:
        """所有窗的进程内设置页过一遍关闭闸门；False = 调用方必须取消这次退出。"""
        for instance in list(getattr(self, "_instances", None) or ()):
            if not self._settings_window_releasable(instance):
                return False
        return True

    def request_app_quit(self) -> None:
        """托盘/恢复窗的「退出应用」：进程内设置页先过关闭闸门，用户要留在设置里就取消整次退出。

        只接**用户显式**的退出入口（托盘「退出」/「退出应用」、恢复窗按钮）。OS 强制会话
        结束与程序内其它 ``app.quit()`` 不走这里：``aboutToQuit`` → ``_on_session_end`` 那条
        路径保持既有语义——**不弹任何阻塞对话框**、照旧收口（见 docs/ISSUE-111-*）。
        """
        if not self._release_settings_windows():
            logging.info("退出应用：用户留在设置页里继续改，本次退出取消")
            return
        self.app.quit()

    def _close_instance_subwindows(self, instance: PetInstance) -> None:
        """批5.2 P1-5：关闭本窗拥有的聊天窗/设置窗并断开引用。

        ChatWindow 自 4.4.0 起**关闭即销毁**（`pet/chat/lifecycle.py`）：close()
        收尾后由 `done()` 排队 deleteLater，`finished` 信号同时把实例上的缓存槽
        清空。这里仍显式关闭 + 清引用，是为了覆盖「程序内直接退出这只」这条
        不走用户关窗的路径（且 writer 关闭后残留顶层窗会复活写盘 worker）。

        设置页（``modern_settings_dialog``）正常情况下已经在收口**之前**由
        ``_settings_window_releasable`` 过完闸门并断开引用；这里再列一次是防御
        （那条闸门只覆盖用户显式退出入口，不覆盖程序内直接调用本方法的老路径）。
        """
        for attr in ('legacy_chat_window', 'modern_chat_window', 'quick_chat',
                     'chat_settings_dialog', 'modern_settings_dialog'):
            dialog = getattr(instance, attr, None)
            if dialog is None:
                continue
            try:
                dialog.close()
            except Exception:
                logging.exception("退出这只：关闭子窗失败 (%s)", attr)
            try:
                # N-4：WA_DeleteOnClose 的对话框 close() 已调度销毁，再排
                # deleteLater 会对已删 C++ 对象抛 RuntimeError（噪音）。
                # 聊天窗的销毁由 lifecycle 接管（_release_scheduled）：它会等在飞
                # worker 退出再删，这里补一刀会赶在 worker 之前删掉宿主对象。
                if (not dialog.testAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
                        and not getattr(dialog, "_release_scheduled", False)):
                    QTimer.singleShot(0, dialog.deleteLater)
            except Exception:
                pass
            setattr(instance, attr, None)
        instance.chat_window = None

    def _close_instance_session_writer(self, instance: PetInstance) -> None:
        """只关本窗（sessions-slot-N）的会话写盘 writer，不置永久屏障。

        R5/E2：多窗下绝不能 close_all_writers(permanent=True)——那会永久关掉
        其它窗的写盘 worker。此处只 flush + 关闭本窗根目录对应的 writer。
        """
        try:
            from .chat import session_store as _session_store
            root = _session_store.SessionStore(
                instance.config.dir, instance.config.instance_id).root
            # P1-7：运行期关窗 writer 的 timeout 降到 2s（不许冻 GUI 10s）；
            # 退出路径的 close_all_writers 仍保持默认 10s 不变。
            if not _session_store.close_writer_for_root(root, timeout=2.0):
                logging.warning("退出这只：会话写盘 worker 未干净关闭 (%s)", root)
        except Exception:
            logging.exception("退出这只：关闭会话写盘 worker 失败")

    def _build_empty_pet_tray(self) -> None:
        """零宠物的可见兜底：托盘（无托盘时另开恢复窗）。

        **必须留一条 WARN**：legacy 拓扑下走这条路径意味着"进程活着、但桌面上没有
        任何顶层窗口"，而调用方不区分它与正常启动——没有日志时用户侧只能报出
        「启动后什么都没有」，连是不是启动失败都无从自查（issue #286）。
        """
        logging.warning(
            "legacy: slot0 未活跃，没有捕获窗（活跃集合=%s，配置目录=%s）："
            "托盘/恢复窗的「生成 / 恢复 slot0」可重建",
            sorted(getattr(self, "_active_pet_slots", None) or ()),
            getattr(self.instance.config, "dir", None))
        tray = self.tray
        if tray is None:
            tray = QSystemTrayIcon(self._tray_placeholder_icon())
            self.tray = tray
        menu = QMenu()
        menu.addAction("slot0 未活跃：没有捕获窗").setEnabled(False)
        menu.addAction("生成 / 恢复 slot0", self.spawn_pet)
        menu.addAction("slot0 设置", self.instance.open_modern_settings)
        menu.addAction("退出应用", self.request_app_quit)
        self._install_tray_menu(menu, [menu])
        tray.setContextMenu(menu)
        # 托盘提示同样说实话：默认的通用 tooltip 会让"桌面上什么都没有"看起来像
        # 悬停没反应，而不是"这只宠物当前没有捕获窗"。
        tray.setToolTip("dsh-pet：没有可显示的桌宠（slot0 未活跃）")
        tray.show()
        if not QSystemTrayIcon.isSystemTrayAvailable():
            from PySide6.QtWidgets import QWidget, QVBoxLayout, QPushButton

            recovery = getattr(self, "_pet_recovery_window", None)
            if recovery is None:
                recovery = QWidget()
                recovery.setWindowTitle("桌宠恢复")
                layout = QVBoxLayout(recovery)
                for label, callback in (("生成 / 恢复 slot0", self.spawn_pet),
                                        ("slot0 设置", self.instance.open_modern_settings),
                                        ("退出应用", self.request_app_quit)):
                    button = QPushButton(label)
                    button.clicked.connect(callback)
                    layout.addWidget(button)
                self._pet_recovery_window = recovery
            recovery.show()

    def _refresh_tray_menu(self) -> None:
        """重建单托盘的菜单，逐窗列出「显示/隐藏」「退出这只」（批5.2 spike 聚合）。

        多窗时不新建托盘，只复用主窗托盘并刷新菜单；聚合美化留到 5.2a。
        """
        if self.tray is None:
            return
        primary = self._instances[0] if self._instances else None
        win = primary.win if primary is not None else None
        if win is None:
            return
        self._build_tray(win, tray=self.tray)

    def _toggle_primary_pet_visible(self) -> None:
        """双击托盘图标：切换主窗（instances[0]）可见性（按当前主窗）。"""
        primary = self._instances[0] if self._instances else None
        win = primary.win if primary is not None else None
        if win is None:
            return
        if win.isVisible():
            win.hide()
        else:
            win.show()
        if getattr(self, "island", None) is not None:
            self.island.set_pet_visible(self._aggregate_pet_visible())

    def _install_macos_dock_menu(self) -> QMenu | None:
        """Install the native Dock context menu as an independent recovery path."""
        if sys.platform != "darwin":
            self.dock_menu = None
            return None
        menu = QMenu()

        def show_pet() -> None:
            if self.instance is None:
                return
            win = self.instance.win
            if win is None:
                return
            win.show()
            win.raise_()
            if getattr(self, "island", None) is not None:
                self.island.set_pet_visible(True)

        # N-2：Dock 菜单只装一次，动作必须动态解析当前主窗实例——
        # 主窗经「退出这只」退掉并提升新主窗后，绑旧实例会把死窗复活。
        menu.addAction("显示桌宠", show_pet)
        menu.addAction("桌宠设置", lambda: self.instance is not None and self.instance.open_modern_settings())
        if self.enable_chat:
            menu.addAction("AI 对话", lambda: self.instance is not None and self.instance.open_chat())
        menu.addSeparator()
        quit_callback = getattr(self.app, "quit", None)
        if callable(quit_callback):
            menu.addAction("退出", quit_callback)
        install_dock_menu = getattr(menu, "setAsDockMenu", None)
        dock_menu_installed = callable(install_dock_menu)
        if dock_menu_installed:
            install_dock_menu()
        menu.setProperty("dockMenuInstalled", dock_menu_installed)
        self.dock_menu = menu
        return menu

    def open_todo_panel(self) -> None:
        """打开待办管理面板（非模态单例；条目增删改即时落盘）。"""
        if not feature_activation_mod.is_active("todo"):
            # 模块未激活：菜单层已投影过滤，这里是绕过菜单直达回调的兜底
            # （旧布局残留 / 通知点击回跳 / 外部触发）。面板一开就会懒创建
            # 服务并把 30s tick 跑起来，所以闸必须在懒创建之前。
            logging.info("待办模块未激活，忽略「待办提醒」请求")
            return
        from .todo.panel import TodoPanelDialog

        # Phase 1：即使总开关关闭，用户主动打开面板也需要服务对象（懒创建）。
        self._ensure_todo_service()
        if self.todo_panel is None:
            # parent 必须是 QWidget：overlay 拓扑下 self.win 是 sprite 壳（QObject），
            # 直接当 parent 会在 QDialog 构造期 TypeError。
            parent = self.win
            dialog = TodoPanelDialog(
                self, parent=parent if isinstance(parent, QWidget) else None)
            # 已是「关闭即销毁」形态（不必再改）：探针实测关窗后存活控件回到 0、
            # 槽位清空、重开是新对象且不残留 pet 分配（
            # .scratch/chat-window-close/probe_window_lifetime.py）。
            dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
            dialog.finished.connect(self._todo_panel_finished)
            self.todo_panel = dialog
        # 展示走主窗实例的 _present_dialog（复用其置顶/聚焦与防重入逻辑）
        inst = self.instance
        if inst is not None:
            inst._present_dialog(self.todo_panel)
        else:
            self.todo_panel.show()

    def _todo_panel_finished(self, _result: int) -> None:
        self.todo_panel = None

    def open_network_info(self) -> None:
        """打开「网络信息」弹窗（非模态单例；数据只在打开时取一次，不轮询）。"""
        from .network_info_dialog import NetworkInfoDialog

        dialog = getattr(self, "network_info_dialog", None)
        if dialog is not None and not shiboken6.isValid(dialog):
            # 已销毁但收口信号还没到（或父宠物先走一步带走子窗）：先清掉再新建，
            # 否则复用路径会在死对象上抛 "Internal C++ object already deleted"。
            dialog = None
            self.network_info_dialog = None
        if dialog is None:
            # parent 必须是 QWidget：overlay 拓扑下 self.win 是 sprite 壳（QObject），
            # 直接当 parent 会在 QDialog 构造期 TypeError（同待办面板）。
            parent = self.win
            dialog = NetworkInfoDialog(parent if isinstance(parent, QWidget) else None)
            dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
            dialog.finished.connect(self._network_info_finished)
            # 父窗销毁（或任何 C++ 侧析构）不一定发 finished，但一定会发 destroyed：
            # 用带对象身份的收口兜住，避免单例长期指向失效对象。
            dialog.destroyed.connect(
                lambda *_args, owner=self, target=dialog:
                    owner._network_info_destroyed(target))
            self.network_info_dialog = dialog
        inst = self.instance
        if inst is not None:
            inst._present_dialog(dialog)
        else:
            dialog.show()

    def _network_info_finished(self, _result: int) -> None:
        """正常关闭（关窗即释放单例；必要时由 destroyed 再兜一次）。"""
        self.network_info_dialog = None

    def _network_info_destroyed(self, dialog) -> None:
        """窗口被销毁时收口单例；**只认当前持有的那一个**。

        身份核对不可省：旧窗的 destroyed 可能晚于新窗创建（先销毁、再点菜单开新窗），
        无脑清空会把新实例一起丢掉，之后每次点击都新建一个窗口。
        """
        if getattr(self, "network_info_dialog", None) is dialog:
            self.network_info_dialog = None

    def trigger_voice_chime_now(self, text: str = "") -> None:
        """手动报时：设置页试听入口与菜单编排加回的「立即报时」共用（该菜单项默认隐藏）。

        无论报时总开关是否开启都会执行（试听/手动触发语义），服务懒创建。
        """
        service = self._ensure_chime_service()
        service.say_now(text=text)

    def _toggle_flag(self, key: str, sync) -> None:
        """布尔开关的统一实现：翻转配置 → 落盘 → 同步服务启停。

        语音报时/节日提醒两个开关此前逐字同构（读旧值取反、save、调各自的
        `_sync_*_service`），这里收成一处；读旧值的口径（`bool(get(...))`
        后取反）与落盘时机逐点不变。
        """
        self.config.set(key, not bool(self.config.get(key, False)))
        self.config.save()
        sync()

    def toggle_voice_chime(self) -> None:
        """「启用/关闭语音报时」开关（默认隐藏、菜单编辑器可加回）：翻转配置并同步服务启停。"""
        self._toggle_flag("voice_chime_enabled", self._sync_chime_service)

    def trigger_festival_now(self) -> None:
        """手动提醒「今日节日」：设置页「立即试听」与菜单编排加回的「今日节日」共用（该菜单项默认隐藏）。

        与语音报时的手动触发同语义——**无视总开关**，服务懒创建；当天没有
        节日/节气时给出明确文案，不做静默无反应。
        """
        if not feature_activation_mod.is_active("festival"):
            # 本入口明确**绕过提醒总开关**（试听语义），所以模块闸必须单独在这里：
            # 否则模块停用后菜单/设置页试听/独立设置进程三条路都能把服务与整套
            # 文案表（2.95MB）拉起来。
            logging.info("节日模块未激活，忽略「今日节日」请求")
            return
        service = self._ensure_festival_service()
        service.remind_now()

    def toggle_festival_reminder(self) -> None:
        """「启用/关闭节日提醒」开关（默认隐藏、菜单编辑器可加回）：翻转配置并同步服务启停。"""
        self._toggle_flag("festival_reminder_enabled", self._sync_festival_service)

    # ------------------------------------------------------------ 每日一签（互动 · 趣味）
    def _ensure_divination_service(self):
        """懒创建抽签服务（纯逻辑 + 无定时器，创建很轻，但仍按开关懒建）。"""
        if getattr(self, "divination_service", None) is None:
            from .divination.service import DivinationService

            self.divination_service = DivinationService(self)
            # 建好就刷一次配置：`apply_config()` 会顺手把今天的语音预合成提交掉，
            # 免得"服务建了但没人调 apply_config"时预热被漏掉。
            self.divination_service.apply_config()
        return self.divination_service

    def _sync_divination_service(self) -> None:
        """按开关同步抽签服务：关掉就释放，不留常驻对象。"""
        from .divination import normalize_divination_config

        # 模块闸与行为开关合成一个判据：未激活时不建服务（不装 10 分钟跨天定时器、
        # 不提交预合成/重试链）；已在跑的走下面的 else 分支显式 shutdown()。
        if feature_activation_mod.is_active("divination") and normalize_divination_config(
            self.config
        )["enabled"]:
            self._ensure_divination_service().apply_config()
        elif getattr(self, "divination_service", None) is not None:
            # 停用要**显式收口**：服务持有跨天定时器，直接丢对象定时器还会留着。
            # shutdown() 同时把代次 +1，已投递的揭签/预热重试闭包整条作废。
            self.divination_service.shutdown()
            self.divination_service = None

    def trigger_divination_draw(self) -> None:
        """菜单「抽支签」：出当天的签（当天再点显示同一支）。

        关掉总开关时**不做静默无反应**——明确告诉用户去哪儿打开（天气那条同口径）。
        """
        if not feature_activation_mod.is_active("divination"):
            # 模块未激活：菜单层已投影过滤，这里是绕过菜单直达回调的兜底
            # （旧布局残留 / overlay 门面 / 外部触发）。本方法会**懒创建**服务并
            # apply_config()，闸必须在这之前，否则调一次就把服务与定时器复活了。
            logging.info("抽签模块未激活，忽略「抽支签」请求")
            return
        service = self._ensure_divination_service()
        service.apply_config()
        if service.draw_now() is None:
            win = getattr(self, "win", None)
            if win is not None:
                try:
                    win.show_bubble("每日一签已关闭 · 设置 → 互动 · 趣味 里打开", duration_ms=6000)
                except Exception:
                    logging.exception("抽签关闭提示气泡失败")

    def divination_hint_on_interaction(self) -> None:
        """用户点了桌宠：当天还没看签时冒一行提示（服务内部判开关与是否已抽）。"""
        service = getattr(self, "divination_service", None)
        if service is None:
            return
        try:
            service.hint_on_interaction()
        except Exception:
            logging.exception("抽签提示失败")

    def system_notify(self, title: str, message: str, *, on_click=None, duration_ms: int = 5000) -> None:
        """Show a bottom-right desktop notification (self-drawn, tray-independent)."""
        self._prune_toasts()
        toast = DesktopNotification(
            str(title),
            str(message),
            on_click=on_click,
            duration_ms=int(duration_ms),
        )
        self._toast_windows.append(toast)
        toast.destroyed.connect(lambda _obj=None: self._prune_toasts())
        toast.show()
        position_stack(self._toast_windows)

    def _prune_toasts(self) -> None:
        self._toast_windows = [
            w for w in self._toast_windows
            if not (hasattr(w, "is_closed") and w.is_closed())
        ]
        position_stack(self._toast_windows)

    def _tray_placeholder_icon(self) -> QIcon:
        """首帧还没解码时的托盘占位图标：复用既有矢量图标语言，不用新素材。

        刻意不把桌宠窗口传进去取主题色——_build_tray 的调用方可能是非 QWidget
        的替身窗口（测试），而托盘观感本来就该跟窗口 QSS 无关。
        """
        return vector_menu_icon(QWidget(), "pet", 64)

    def _build_tray(self, win: PetWindow, tray: QSystemTrayIcon | None = None) -> QSystemTrayIcon:
        # 批5.2：可复用已有托盘（_refresh_tray_menu 传 self.tray），避免多窗各自
        # 建托盘图标；新建时绑定双击切换，复用时不重复连接（activated 只接一次）。
        if tray is None:
            icon = QIcon(win.icon_pixmap())
            pending = icon.isNull()
            if pending:
                # 首帧还没解码时 icon_pixmap() 是空图：直接拿去建托盘会得到一个
                # 没有图标的条目（用户反馈「托盘图标消失了」，且此后永不刷新）。
                # 先用占位图标顶上，首帧就绪后再换角色头像。
                icon = self._tray_placeholder_icon()
            tray = QSystemTrayIcon(icon)
            tray.activated.connect(
                lambda reason: self._toggle_primary_pet_visible()
                if reason == QSystemTrayIcon.ActivationReason.DoubleClick
                else None
            )
            if pending:
                ready = getattr(win, "frame_ready", None)
                if ready is not None:
                    ready.connect(lambda: tray.setIcon(QIcon(win.icon_pixmap())))

        def toggle_visible() -> None:
            if win.isVisible():
                win.hide()
            else:
                win.show()
            if getattr(self, "island", None) is not None:
                self.island.set_pet_visible(win.isVisible())

        menu = QMenu()
        # F5：本 build 创建的全部 QMenu（含子菜单）先收集起来，安装时由
        # _install_tray_menu 显式接管所有权（进程侧强引用保活 + 旧菜单
        # 在新菜单接管后才释放），防止 wrapper 被回收导致菜单/子菜单被误判删除。
        tray_submenus: list[QMenu] = [menu]

        def track_menu(child: QMenu) -> QMenu:
            tray_submenus.append(child)
            return child

        # 气泡是置顶 Tool 窗口（层级高于原生菜单 popup），托盘菜单弹出前
        # 先隐藏气泡，避免气泡盖住菜单
        menu.aboutToShow.connect(lambda: win.hide_speech_bubble())

        def add_tray_toggle(target: QMenu, label: str, checked, apply):
            """可勾选托盘条目：勾选态照旧即时翻转，副作用延到菜单关闭后。

            ``add_tray_action`` 的回调在菜单关闭后执行，此时 ``isChecked()`` 已是
            用户点出来的最终值——故 apply 收勾选态，而不是在连接时把它绑死。
            勾选态的接线保持原样（``setCheckable`` + 初始 ``setChecked`` + 弹出前
            ``sync_tray_checks`` 同步）；旧接线挂 ``toggled`` 连副作用，弹出前的那次
            程序化 ``setChecked`` 也会把它触发一遍（开一次菜单就把配置/系统登录项
            重写一遍），挂 ``triggered`` 没有这个副作用。
            """
            action = add_tray_action(target, label, lambda: apply(bool(action.isChecked())))
            action.setCheckable(True)
            action.setChecked(bool(checked))
            return action

        # 本段全部条目都会动窗口面（显示/激活桌宠窗、重建角色窗面、开关岛窗）或
        # 冒泡/系统登录项：托盘菜单是原生弹出（macOS 上是 NSMenu 跟踪会话），
        # 会话中做这些会被 AppKit 抑制（首次点击无反应，issue #4 家族），故一律
        # 走 add_tray_action——菜单先关，回调延到菜单真的不在之后再执行。
        add_tray_action(menu, '显示 / 隐藏', toggle_visible)
        add_tray_action(menu, '回到右下角', lambda: win.go_default_corner())

        def toggle_island(enabled: bool) -> None:
            island_cfg = dict(self.config.get("dynamic_island", {}) or {})
            island_cfg["enabled"] = bool(enabled)
            self.config.set("dynamic_island", island_cfg)
            self.config.save()
            self._sync_dynamic_island()

        island_action = None
        if feature_activation_mod.is_active("dynamic_island"):
            # 灵动岛模块未激活：这条直写配置 + _sync_dynamic_island 的托盘开关整条
            # 不出现（它是绕过设置页的第二条入口，留着就是「界面藏了、开关还在」）。
            island_action = add_tray_toggle(
                menu, '灵动岛',
                bool(self.config.get("dynamic_island", {}).get("enabled", True)),
                toggle_island)

        if self.enable_chat:
            add_tray_action(menu, 'AI 对话', self.instance.open_chat)
            add_tray_action(menu, '快速对话（气泡）', self.instance.open_quick_chat)
            add_tray_action(menu, 'AI 设置', self.instance.open_chat_settings)
        add_tray_action(menu, '桌宠设置', self.instance.open_modern_settings)

        m_char = track_menu(menu.addMenu('切换角色'))
        # 勾选态标**当前身份**的角色（换角色后本菜单会重建，见 _refresh_tray_menu）；
        # 读进程级配置会让勾永远停在出厂默认那只（与岛名同源缺陷）
        current = str(self._identity_config().get('character', catalog.DEFAULT_CHARACTER))
        for cid in catalog.list_available_characters():
            act = add_tray_action(
                m_char, cid, lambda cid=cid: self.instance.switch_character(cid))
            act.setCheckable(True)
            act.setChecked(cid == current)

        mouse_through = add_tray_toggle(
            menu, '鼠标穿透', bool(self.config.get('mouse_through', False)),
            win.set_mouse_through)

        menu.addSeparator()

        auto = add_tray_toggle(
            menu, '开机自启', autostart_mod.is_enabled(),
            lambda enabled: self.instance._set_autostart(enabled, win))

        def sync_tray_checks() -> None:
            # 设置对话框/右键菜单里改过的开关，弹出托盘菜单前同步复选状态
            #（托盘菜单在 _build_tray 时一次性构建，不复用则不刷新会过期）。
            # 只改看相、不触发副作用：副作用挂 triggered，程序化 setChecked 不发它。
            mouse_through.setChecked(bool(self.config.get('mouse_through', False)))
            auto.setChecked(autostart_mod.is_enabled())
            if island_action is not None:
                island_action.setChecked(bool(
                    self.config.get("dynamic_island", {}).get("enabled", True)
                ))

        menu.aboutToShow.connect(sync_tray_checks)

        menu.addSeparator()
        if self.enable_chat:
            if feature_activation_mod.is_active("dynamic_island"):
                # 余额属灵动岛模块：模块停用时不给一个"点了没反应"的入口
                menu.addAction('DeepSeek 余额', lambda: self.show_balance(win))
            if feature_activation_mod.is_active("system_tools"):
                # dsh 服务管理属系统工具模块（与 chat 无关，只挂构建能力分支）
                menu.addAction('启动 dsh web 界面', lambda: launch_harness_gui(win, target="web"))
                menu.addAction('启动桌面端界面', lambda: launch_harness_gui(win, target="desktop"))
        else:
            # 纯桌宠版本不提供本地 DSH 启动入口，只保留网页版入口
            menu.addAction('打开网页版 DeepSeek', open_deepseek_web)
        menu.addAction('检查更新', lambda: self.check_update(win))

        # 批5.2a §③.3：多窗时单托盘 + 每窗一个子菜单（显示/隐藏、切换角色、退出这只），
        # 替代 spike 的平铺菜单项；图标仍单托盘，逐窗动作经子菜单路由。
        if len(self.instances) > 1:
            menu.addSeparator()
            for inst in self.instances:
                win_i = inst.win
                if win_i is None:
                    continue
                slot_label = f"[slot-{inst.slot_id}]" if inst.slot_id is not None else ""
                sub = track_menu(menu.addMenu(f'桌宠 {slot_label}' if slot_label else '桌宠'))

                def _toggle(win=win_i) -> None:
                    if win.isVisible():
                        win.hide()
                    else:
                        win.show()

                sub.addAction('显示 / 隐藏', _toggle)
                sub.addAction('回到右下角', lambda w=win_i: w.go_default_corner())
                # 每窗独立的切换角色（读各自 config 的 current character）
                m_char = track_menu(sub.addMenu('切换角色'))
                cur = str(inst.config.get('character', catalog.DEFAULT_CHARACTER))
                for cid in catalog.list_available_characters():
                    act = m_char.addAction(cid)
                    act.setCheckable(True)
                    act.setChecked(cid == cur)
                    act.triggered.connect(
                        lambda checked=False, cid=cid, inst=inst: inst.switch_character(cid))

                def _exit(inst=inst) -> None:
                    self._on_window_exit_requested(inst)

                sub.addAction('退出这只', _exit)

        menu.addAction('退出', self.request_app_quit)

        tray.setContextMenu(menu)
        tray.setToolTip('dsh-pet 独立桌宠')
        tray.show()
        # F5：菜单已由新菜单接管后，显式记录所有权并释放被替换的旧菜单
        #（owner 生命周期：强引用保活到替换，旧菜单延迟销毁防泄漏）。
        self._install_tray_menu(menu, tray_submenus)
        return tray

    def _install_tray_menu(self, menu: QMenu, submenus: list[QMenu]) -> None:
        """显式接管托盘上下文菜单所有权（F5：owner 与替换/销毁顺序）。

        旧菜单必须先等新菜单 ``tray.setContextMenu(menu)`` 接管完成才释放：
        先 ``deleteLater`` 旧菜单、再记录新菜单的强引用集合，保证任何时刻
        托盘引用的菜单都有一份进程侧 Python 引用（PySide6 wrapper 不被回收），
        且被替换的旧菜单经事件循环延迟销毁，不会永久泄漏。

        除菜单本体外，还必须保活每个菜单的 QAction wrapper：子菜单的 menuAction
        挂在父菜单的 actions 列表里；这些 QAction wrapper 一旦被 GC 终结，对应
        QMenu 的 wrapper 即使仍被强引用也会被 PySide6 连带标记为已删除（读取
        sub.actions() 抛 Internal C++ object already deleted）。因此这里一并
        快照保活全部菜单的 actions，外部临时持有/丢弃 actions 列表不再造成失效。
        """
        old = self._tray_menu
        if old is not None:
            old.deleteLater()
        self._tray_menu = menu
        self._tray_submenus = list(submenus)
        snapshot: list = []
        for m in (menu, *submenus):
            try:
                snapshot.extend(m.actions())
            except RuntimeError:
                # 菜单刚被接管，正常不会走到；防御性跳过避免拖垮托盘刷新
                continue
        self._tray_actions = snapshot


def _default_xcb_platform_on_wayland() -> None:
    """Linux Wayland 会话下把 Qt 平台插件默认设为 xcb（XWayland）。

    Wayland 协议不允许客户端自行移动顶层窗口，桌宠拖动依赖的
    QWidget.move() 会被合成器静默忽略（表现为无法拖动）；透明无边框
    窗口在原生 wayland 插件下还存在重绘残留（拖影）。须在创建
    QApplication 之前调用。用户显式设置 QT_QPA_PLATFORM 时尊重其选择。
    """
    if not sys.platform.startswith("linux"):
        return
    if "QT_QPA_PLATFORM" in os.environ:
        return
    if os.environ.get("WAYLAND_DISPLAY") or os.environ.get("XDG_SESSION_TYPE") == "wayland":
        os.environ["QT_QPA_PLATFORM"] = "xcb"


def _configure_linux_fcitx_input_method() -> None:
    """为 PySide6 冻结版选择与内置 Qt ABI 兼容的 Fcitx 输入法前端。"""
    # Linux 成品随包携带按 PySide6 Qt ABI 编译的 Fcitx 插件；未指定时默认选中 fcitx 上下文。
    if not sys.platform.startswith("linux"):
        return
    if os.environ.get("XMODIFIERS", "").strip() != "@im=fcitx":
        return
    if not os.environ.get("QT_IM_MODULE", "").strip():
        os.environ["QT_IM_MODULE"] = "fcitx"


# Historical public name retained for integrations and older tests.
PetApp = AppShell


def main(argv: list[str] | None = None, enable_chat: bool = True) -> int:
    _default_xcb_platform_on_wayland()
    # 必须在 QApplication 构造前设置，Qt 才会按随包 Fcitx 插件创建输入法上下文。
    _configure_linux_fcitx_input_method()
    argv = list(argv if argv is not None else sys.argv)
    preferred_slot = None

    if "--slot" in argv:
        index = argv.index("--slot")
        if index + 1 < len(argv):
            try:
                preferred_slot = int(argv[index + 1])
                if preferred_slot < 0 or preferred_slot > 128:
                    logging.error("无效的 --slot 参数 (必须在 0~128 范围内): %s", argv[index + 1])
                    return 1
            except ValueError:
                logging.error("无效的 --slot 参数: %s", argv[index + 1])
                return 1
        else:
            logging.error("缺少 --slot 参数值")
            return 1

    app = QApplication(argv)
    from .context_menus.menu_styles.common import install_responsive_menu_style

    install_responsive_menu_style(app)
    app.setApplicationName(APP_DIR_NAME)
    app.setQuitOnLastWindowClosed(False)
    # 图片解码上限（像素级别的一处设定）：Qt 默认 256 MB/张（本机 Qt 6.11.2 实测），
    # 用户给的超大图会**直接读不出来**而不是慢一点——聊天附件预览、设置页头像、
    # 趣味图片弹窗都吃这条上限。踩点放在这里而不是各调用点：它是静态设置，
    # 一处设定覆盖全进程（与本文件里其它 Qt 全局设定同一位置纪律）。
    from .qt_image_limits import apply_image_allocation_limit
    apply_image_allocation_limit()

    # 确定配置根目录
    config_dir = _default_base() / APP_DIR_NAME

    # D4（PHASE4_DESIGN §4）桌宠单实例进程门：**先于** slot 竞争。两个拓扑都抢
    # （T4 Phase A）：overlay 双开 = 两个进程各自恢复全部 sprite、位置回写互踩；
    # 捕获模式的 legacy 单窗拓扑同样是完整实例（建窗、存位置、落盘配置），双开
    # 与「重启时多只桌宠先收拢为主宠物」的单窗口径冲突。第二实例必须在产生任何
    # 配置目录副作用（抢 slot-1、落种 config-slot-N.json）之前退场。
    overlay_gate = overlay_gate_mod.acquire_overlay_instance_gate(config_dir)
    if not overlay_gate.acquired:
        # 双击桌面宠物场景：不弹窗、不阻塞，只留痕 + 退出。退出码对齐
        # pet/__main__.py 的 settings.lock 惯例（「已有实例，本次退出」= 0），
        # 免得外壳把"第二实例"当启动失败报错。
        logging.warning(
            "已有实例在运行（门 %s，持有者 pid=%s）：本次启动退出",
            overlay_gate.path, overlay_gate.holder_pid(),
        )
        return overlay_gate_mod.DUPLICATE_EXIT_CODE

    # 4.4a 停用多进程多宠退役层的**槽位文件锁**：main() 不再抢 slot-N.lock
    #（原语义 = 跨进程竞争「我是第几只」，退役层删除后不复存在）。`--slot`
    # 兼容解析保留（T5）：旧快捷方式/自启项仍按它选择 config-slot-N.json
    # 身份；缺省或 D4 门保证的单实例即主身份（slot 0）。
    slot_id = preferred_slot if preferred_slot is not None else 0

    try:
        instance_id = ""
        os.environ["DSH_PET_INSTANCE"] = ""
        slot_manager_mod.migrate_legacy_spawns(config_dir)
        config = Config(instance_id="")
        _mac_set_dock_icon_visible(bool(config.get("show_dock_icon", True)))
        _setup_logging(config)
        # 功能模块激活：在创建任何可选服务（AppShell 及其服务树）之前完成
        # 一次性迁移判定并冻结本次运行快照；功能页写盘只影响下次启动。
        feature_activation_mod.decide_and_freeze(config)

        logging.info("dsh-pet-standalone 启动 (slot: %s, instance: %s)", slot_id, instance_id)
        _cleanup_stale_runtime_dirs()
        stale_removed = autostart_mod.cleanup_stale_entries()
        if stale_removed:
            logging.info("已清理 %d 个指向不存在路径的开机自启项", stale_removed)

        controller = AppShell(app, config, enable_chat=enable_chat, slot_id=slot_id,
                              spawn_offset=_read_spawn_offset_env(), overlay_gate=overlay_gate)
        try:
            controller.start()
        except Exception as exc:
            logging.exception("启动失败")
            _show_startup_error("dsh-pet-standalone", str(exc))
            return 1

        logging.info("进入事件循环")
        return app.exec()
    finally:
        # D4：正常退出（app.exec() 返回前 aboutToQuit 已跑完，各窗位置已落盘）
        # 才释放进程门——比 _on_about_to_quit 内更晚，避免"本地还在收尾、第二
        # 实例已能起来"的位置回写竞态。会话结束路径另见 _on_session_end。
        if overlay_gate is not None:
            try:
                overlay_gate.release()
            except Exception:
                logging.exception("释放桌宠单实例进程门失败")


if __name__ == '__main__':
    sys.exit(main())
