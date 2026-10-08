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

import hashlib
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

from . import autostart as autostart_mod
from . import catalog
from . import click_sound
from . import overlay_instance_gate as overlay_gate_mod
from . import self_talk_voice
from . import slot_manager as slot_manager_mod
from . import updater
from . import webm_clip as webm_clip_mod
from .config import APP_DIR_NAME, Config, _default_base
from .context_menus.icons import vector_menu_icon
from .context_menus.shared import open_deepseek_web
from .desktop_notify import DesktopNotification, position_stack
from .harness_launcher import launch_harness_gui
from .library import MovieLibrary
from .window import PetWindow
from .fun_image_popup import restore_ojingjing_windows
from .runtime_cleanup import cleanup_stale_runtime_dirs
from .session_watcher import install_session_watcher
from .decode_fanout import DecodeFanoutHub
from .todo_reminder import TodoReminderService
from .voice_chime_service import VoiceChimeService
from .double_click_chat import install_double_click_chat
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

    pet.balance 按需导入（内存瘦身第二刀）：该模块自带整套档位文案/价格提示
    表，实测独占 4.75MB Python 堆（tools/import_cost.py），而余额查询在
    默认配置下从不发生（balance_refresh_minutes=0 且用户没点"看余额"）——
    启动就不该为它付这份常驻。函数内 import 只在真正要显示余额时才付。
    """
    from . import balance as balance_mod

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


def _warm_music_player_paths() -> None:
    """后台线程暖音乐播放器路径缓存（幂等，可重复调用）。

    右键菜单「打开网易云/QQ音乐并播放」的 builder 在 GUI 线程只读缓存（冷缓存
    时乐观启用）：真正的目录浅扫在这里的 daemon 线程里做，否则冷缓存下首次开
    菜单要在 MainThread 上扫 6s+。
    """
    from . import music_players

    for player_key in ("netease", "qqmusic"):
        music_players.warm_cache_async(player_key)


def _schedule_music_player_warm() -> None:
    """UI 就绪后统一预热点的一环：延迟几秒再后台暖播放器路径缓存。

    用单次 QTimer 错峰，**不阻塞任何线程**（这里只是登记一个延迟任务）。
    """
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
        win.on_open_chat = self._slot_wrap(self.open_chat) if self.enable_chat else None
        win.on_open_quick_chat = self._slot_wrap(self.open_quick_chat) if self.enable_chat else None
        win.on_open_chat_settings = self._slot_wrap(self.open_chat_settings) if self.enable_chat else None
        win.on_show_balance = self._slot_wrap(self.shell.show_balance) if self.enable_chat else None
        win.on_check_update = self._slot_wrap(self.shell.check_update)
        win.on_look_synced = self._slot_wrap(self.sync_look_to_chat) if self.enable_chat else None
        win.on_look_screen = win.look_at_screen if self.enable_chat and hasattr(win, "look_at_screen") else None
        win.on_open_legacy_settings = None
        win.on_open_modern_settings = self._slot_wrap(self.open_modern_settings)
        # 桌宠隐藏时的气泡改道面（DSH 联动等非交互反馈气泡 → 灵动岛，见
        # window_alerts.redirect_hidden_bubble）；岛对话不可用时注入方返回 False。
        win.hidden_bubble_redirect = self.shell._island_feedback_bubble
        # 反馈面可用性探针：隐藏期联动监视器是否跳过低功耗暂停（mixin 消费）。
        win.island_feedback_available = self.shell._island_feedback_available
        # 双击桌宠 → 快速对话气泡（事件过滤器，见 pet/double_click_chat.py）。
        # 必须排在 on_open_quick_chat 注入之后；no-chat 变体下该回调为 None，
        # 过滤器自身会完整回落到原有双击行为。
        install_double_click_chat(win)
        win.on_spawn_pet = self._slot_wrap(self.shell.spawn_pet)
        # 「退出子肥鱼」只挂给主肥鱼（instance_id 为空）：子肥鱼进程里该入口的
        # pid==os.getpid() 自我保护会跳过子鱼自己、把主鱼当子鱼 taskkill 掉
        #（实机事故：从子鱼触发清除 → 主鱼被杀、触发的那只子鱼存活）。
        win.on_clear_spawned_pets = (
            self._slot_wrap(self.shell.clear_spawned_pets)
            if not self.config.instance_id else None)
        win.on_open_todo_panel = self._slot_wrap(self.shell.open_todo_panel)
        win.on_voice_chime_now = self._slot_wrap(self.shell.trigger_voice_chime_now)
        win.on_toggle_voice_chime = self._slot_wrap(self.shell.toggle_voice_chime)
        # 点击自言自语的朗读走同一条音频通道（AppShell 持有，进程内唯一）。
        win.on_self_talk_speak = self._slot_wrap(self.shell.speak_self_talk)
        win.on_festival_now = self._slot_wrap(self.shell.trigger_festival_now)
        win.on_toggle_festival = self._slot_wrap(self.shell.toggle_festival_reminder)
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
        if getattr(old_win, 'agent_link_manager', None) is not None:
            old_win.agent_link_manager.shutdown()
        # 主窗热切换才换托盘（进程级单托盘）；非主窗热切换不动共享托盘。
        self._build_window(character_id, lib=lib, build_tray=(self is self.shell.instance))
        if self.enable_chat:
            for chat_window in (self.legacy_chat_window, self.modern_chat_window):
                if chat_window is not None:
                    chat_window.set_pet_window(self.win)
                    chat_window.switch_character(character_id)
        if getattr(self.shell, "island", None) is not None:
            self.shell.island.refresh_from_config()
        # P1-4：任一窗切换后刷新托盘菜单（per-window 区闭包指向新窗，防陈旧窗）
        self.shell._refresh_tray_menu()

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
        if not self.enable_chat or self.win is None:
            return
        # 桌宠全部隐藏：气泡锚不到桌宠，改弹到灵动岛上（岛是对话代理）。
        # singleShot 与下方同口径：Cocoa 菜单跟踪期间延迟到菜单关闭再弹。
        shell = getattr(self, "shell", None)
        if shell is not None and not shell._aggregate_pet_visible() \
                and shell._island_chat_available():
            QTimer.singleShot(0, lambda: shell._show_island_chat(activate=True))
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
            self.quick_chat = QuickChatBubble(
                self.config, pet_window=self.win,
                # 双击/快速对话气泡接 DSH 后端；无 AppShell 的测试替身与
                # no-chat 场景都取不到 tracker，此时气泡完整回落本地 LLM。
                dsh_tracker=getattr(getattr(self, "shell", None), "_dsh_state_tracker", None),
            )
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
        if self.legacy_chat_window is None:
            self.legacy_chat_window = ChatWindow(
                self.config,
                str(self.config.get('character', catalog.DEFAULT_CHARACTER)),
                pet_window=host,
                notifier=self.shell.system_notify,
                auth_callback=self.open_chat_settings,
            )
        else:
            self.legacy_chat_window.set_pet_window(host)
        self.chat_window = self.legacy_chat_window
        self._present_dialog(self.legacy_chat_window, lambda: self.legacy_chat_window.position_near_pet(host))

    def open_modern_chat(self) -> None:
        host = self._chat_host()
        if not self.enable_chat or host is None:
            return
        if self._defer_while_popup_active("modern-chat", self.open_modern_chat):
            return
        from .chat.widgets import ChatWindow
        if self.modern_chat_window is None:
            self.modern_chat_window = ChatWindow(
                self.config,
                str(self.config.get('character', catalog.DEFAULT_CHARACTER)),
                pet_window=host,
                notifier=self.shell.system_notify,
                auth_callback=self.open_chat_settings,
            )
        else:
            self.modern_chat_window.set_pet_window(host)
        self.chat_window = self.modern_chat_window
        self._present_dialog(self.modern_chat_window, lambda: self.modern_chat_window.position_near_pet(host))

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
            character_id = str(self.config.get("character", catalog.DEFAULT_CHARACTER))
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

    def __init__(self, app: QApplication, config: Config, enable_chat: bool = True,
                 slot_id: int | None = None,
                 spawn_offset: int = 0, overlay_gate=None) -> None:
        self.app = app
        self.config = config
        self._enable_chat = bool(enable_chat)
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
        self._on_about_to_quit_connected = False
        # overlay 拓扑产品壳（4.1a）：仅 PET_RENDER_TOPOLOGY=overlay 时构造
        self._overlay_shell = None
        # D4：overlay 拓扑单实例进程门（由 main() 抢到后传入；legacy 拓扑恒为
        # None）。释放挂在会话结束路径（_on_session_end）与 main() 的 finally。
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
        if self._todo_wanted():
            self._ensure_todo_service()
        # 语音报时：进程级单例（多窗共用调度器）。默认关闭（2026-09-19 起，
        # 主动打扰型功能改由用户显式开启）→ 启动不创建；开启后跑 20s tick，
        # 设置关闭后 stop 并释放。edge-tts 合成在后台线程，
        # 播放与气泡走 GUI 线程（QtMultimedia + win.show_bubble）。
        self.voice_chime_service = None
        if self._chime_wanted():
            self._ensure_chime_service()
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
        self.instance = PetInstance(
            self, config, enable_chat=self.enable_chat,
            slot_id=slot_id, spawn_offset=spawn_offset,
        )
        self._instances.append(self.instance)
        from .multi_window_shared import SharedSubsystems

        self._shared = SharedSubsystems(self)  # 4.4b：常开化（T6）
        # 订阅 DSH 统一状态（单读方合并后由共享 Agent 联动管理器转发；收敛逻辑
        # 是 pet/dsh_state.py 的纯函数，读方是 DshMonitor 自己）：
        # thinking/attention 注入与 offline 交互收口在管理器内部完成，这里只
        # 驱动灵动岛的 agent 活跃指示。
        self._shared.agent_link.dsh_state_changed.connect(self._on_dsh_state_changed)
        _LIVE_SHELLS.add(self)

    @property
    def enable_chat(self) -> bool:
        """进程级单源（E2/REVIEW_batch51）：窗口经 PetInstance.enable_chat 转发。"""
        return self._enable_chat

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
        return getattr(self, '_overlay_shell', None)

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
        """节日语音是否开启——它复用报时服务的音频通道，因此会连带影响通道生命周期。"""
        return bool(
            self.config.get("festival_reminder_enabled", False)
            and self.config.get("festival_reminder_speak", False)
        )

    def _self_talk_speak_wanted(self) -> bool:
        """点击自言自语朗读是否开启——它同样复用报时服务的音频通道。

        只要求「点击触发自言自语」+ 本朗读开关：点击自言自语是独立开关，不与
        「气泡自言自语」总开关（周期气泡）耦合，否则只想点击听声的用户永远拿不到
        音频通道。避免给用不到的场景常驻一条音频通道。
        """
        return bool(
            self.config.get("self_talk_speak_enabled", False)
            and self.config.get("click_show_self_talk", False)
        )

    def _chime_wanted(self) -> bool:
        # 报时自身开启，或节日语音 / 点击自言自语朗读需要这条音频通道（三者共用
        # 一套合成与播放，因此"通道是否存在"取决于其中之一是否需要）。
        return (
            bool(self.config.get("voice_chime_enabled", False))
            or self._festival_speak_wanted()
            or self._self_talk_speak_wanted()
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

    def ensure_audio_channel(self):
        """对外暴露的音频通道（节日提醒语音复用报时服务的合成与播放）。"""
        return self._ensure_chime_service()

    def self_talk_voice_file(self, text: str) -> Path | None:
        """预缓存台词音频的查找路径；没有就返回 None。

        命名约定与产出脚本（F:\\dsh\\tts\\build_self_talk_voice.py）一致：
        ``<配置目录>/self_talk_voice/<md5(文本)[:16]>.wav``。刻意放配置目录而不是
        仓库 assets —— 它属于用户数据（换资源包/更新不该丢），与报时缓存
        voice_chime_cache 同级。
        """
        directory = getattr(getattr(self, "config", None), "dir", None)
        if directory is None:
            return None
        name = hashlib.md5(str(text).strip().encode("utf-8")).hexdigest()[:16] + ".wav"
        path = Path(directory) / "self_talk_voice" / name
        try:
            # 0 字节的残file不算命中（合成写入被打断会留下它）：播静音比回落
            # 在线合成更糟——主人只会觉得"点了没声/坏了"，日志里什么都看不到。
            if path.is_file() and path.stat().st_size > 0:
                return path
        except OSError:
            pass
        return None

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
            channel.speak(stripped, log_tag="点击自言自语")
        except Exception:
            logging.exception("点击自言自语语音播报失败")
            return False
        return True

    def precache_self_talk_voice(self, reason: str = "") -> bool:
        """把还没有本地音频的点击台词（含点击动画绑定台词）交给后台补齐。

        为什么必须后台：本机合成一句要 20 秒上下（还要 ASR 回读校验、不满意就
        重抽），保存设置或点击那一刻同步做必然卡顿。开关
        ``self_talk_voice_precache_enabled`` 默认关闭——本机没装本地 TTS 服务是
        常态，不开就不该发探测请求；服务不在线时后台线程安静收工。

        返回是否真的起了新一轮（上一轮没跑完时返回 False，不排队）。
        """
        try:
            started = self_talk_voice.start_precache(self.config)
        except Exception:
            logging.exception("启动台词语音预缓存失败（不影响点击朗读）")
            return False
        if started:
            logging.getLogger("dsh-pet-standalone").info(
                "已开始后台预缓存台词语音%s", f"（{reason}）" if reason else "")
        return started

    def _sync_chime_service(self) -> None:
        """按配置启停语音报时服务；关闭时释放服务对象。"""
        if self._chime_wanted():
            service = self._ensure_chime_service()
            if service.is_running():
                # 已在运行：设置保存只刷新配置，不重置 20s tick。
                service.apply_config()
            else:
                service.start()
        elif getattr(self, "voice_chime_service", None) is not None:
            try:
                self.voice_chime_service.stop()
            except Exception:
                logging.exception("停止语音报时服务失败")
            self.voice_chime_service = None

    # ------------------------------------------------------------ 功能门控（节日提醒）
    def _festival_wanted(self) -> bool:
        # 总开关默认关闭：主动打扰型功能，升级后不应突然冒出来。
        return bool(self.config.get("festival_reminder_enabled", False))

    def _ensure_festival_service(self):
        """懒创建节日提醒服务（仅在开启提醒/手动触发时创建）。

        pet.festival_service 连带 festival/festival_calendar/festival_data 与
        五份节日文案表，实测 2.95MB Python 堆（tools/import_cost.py）。总开关
        默认关闭（主动打扰型功能），关闭时整套文案表都不该常驻——import 收到
        构造点，开启/试听时按需付一次。
        """
        if getattr(self, "festival_service", None) is None:
            from .festival_service import FestivalReminderService

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
        """DSH 联动的功能门：``agent_link.dsh``（默认关）。

        联动开启时桥接插件才安装（``DshMonitor.install_bridge``，关联动即卸载）、
        DshMonitor（桥目录唯一读方：信号分派 + dsh_state 收敛器共用一份解析）
        才由 ``AgentLinkManager.apply_config`` 拉起；联动没开时它 3s 探端口、
        1.5s 读桥目录全是空转。本方法是该门的单一口径，harness 自启/收口也复用它。
        """
        agent_cfg = self.config.get("agent_link", {})
        return bool(agent_cfg.get("dsh", False)) if isinstance(agent_cfg, dict) else False

    # ------------------------------------------------------------ 设置进程隔离
    def _apply_external_config_change(self) -> None:
        """把「配置已在别处落盘」同步到运行期（独立设置进程 / watcher 路径）。

        与进程内对话框 finished 的应用链同源（PetInstance._modern_settings_finished
        在真实壳上委托到这里）；多实例时扇出到每个 PetInstance 的窗口，而不是
        只看主窗——外部改配置的那个实例不一定是主窗。
        """
        for inst in getattr(self, "_instances", []):
            win = getattr(inst, "win", None)
            if win is not None:
                win.refresh_pet_settings()
        overlay_shell = getattr(self, "_overlay_shell", None)
        if overlay_shell is not None:
            refresh = getattr(overlay_shell, "refresh_settings", None)
            if callable(refresh):
                refresh()  # overlay 拓扑的窗口能力重应用（4.1b）
        self._sync_dynamic_island()
        self._apply_balance_timer()
        # Phase 1/2：设置保存后按配置同步可选服务（todo 懒启停）与动画预热
        self._sync_todo_service()
        self._sync_chime_service()
        self._sync_festival_service()
        # 外部配置变更也可能改了 agent_link.dsh（右键菜单开关落盘后同样收敛到
        # 这里）→ 各窗 refresh_pet_settings 链里的 manager.apply_config 会按
        # 同一功能门启停 DshMonitor（桥目录唯一读方）。
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

    def _launch_settings_process(self, instance=None, *, page: str | None = None) -> bool:
        """startDetached 独立设置进程；冻结包与源码运行分流。

        源码运行要走 `-m pet`（工作目录取仓库根），冻结包直接复用 exe 的参数
        分流入口（--settings）——与 --uninstall-cleanup 同一范式。
        """
        from PySide6.QtCore import QProcess

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
        if instance_id and instance_id != (os.environ.get("DSH_PET_INSTANCE") or "").strip():
            # 进程内多窗下第二窗的 instance_id
            # 不等于进程级 env：显式传参，否则独立设置进程会打开主窗的配置。
            # 主窗/独立槽位进程 env 已一致，命令保持 ["--settings"] 原样。
            arguments += ["--instance", instance_id]
        try:
            started, _pid = QProcess.startDetached(program, arguments, workdir)
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
        """(mtime_ns, size) 签名：目录信号不带文件名，用它兜底判断 config 是否变了。"""
        try:
            stat = self.config.path.stat()
        except OSError:
            return None
        return (stat.st_mtime_ns, stat.st_size)

    def _arm_config_reload(self) -> None:
        """重启去抖窗口：窗口内的多次变动合并成一次 reload+应用。"""
        timer = getattr(self, "_config_reload_timer", None)
        if timer is not None:
            timer.start()

    def _on_config_file_changed(self, path: str) -> None:
        if Path(path).name != self.config.path.name:
            return  # 只认本配置文件的 basename（同目录还有 settings.lock 等）
        self._watch_config_file()
        self._arm_config_reload()

    def _on_config_dir_changed(self, path: str) -> None:
        self._watch_config_file()
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
        self.config.reload()
        try:
            self._apply_external_config_change()
        except Exception:
            logging.exception("应用外部配置变更失败")
        finally:
            self._last_config_signature = self._config_signature()

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
        # 拓扑分流（T5/4.1a）：PET_RENDER_TOPOLOGY=overlay（dev flag，唯一
        # 读取点在 overlay_shell.is_overlay_topology）→ 单合成窗产品壳
        # 接管渲染，不建 PetWindow；默认路径逐行不变
        from .overlay_shell import is_overlay_topology
        if is_overlay_topology():
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
            # 拖文件解读：legacy 由 PetWindow.install_file_interpreter 注入
            # （window_optional_services.py:96-105）；overlay 无 PetWindow，
            # 由壳作宿主（cfg/show_bubble/show_alert/resolve_alert 同名面）。
            try:
                from .file_interpret import FileInterpretController
                self._overlay_shell.set_file_interpret_offer(
                    FileInterpretController(self._overlay_shell).offer)
            except Exception:  # noqa: BLE001 - 可选服务，缺失不阻断启动
                logging.exception("overlay: 拖文件解读接线失败")
        else:
            self._create_ui_with_character_fallback(character_id)
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
        # D4：会话结束（关机/注销）路径释放 overlay 单实例进程门。与下面的
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

    def _release_overlay_gate(self) -> None:
        """释放 overlay 单实例进程门（D4；幂等，legacy 拓扑下恒为 no-op）。"""
        gate = getattr(self, "_overlay_gate", None)
        if gate is None:
            return
        try:
            gate.release()
        except Exception:
            logging.exception("释放 overlay 进程门失败")

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

        目标是 dsh web 时全程静默（CREATE_NO_WINDOW 隐藏控制台 +
        open_browser=False，不开浏览器、无弹窗）；目标是桌面端界面时直接打开
        桌面端应用（用户的主力应用，桌宠退出**不会**关它）。

        「本机已有实例」的判定交给 ``launch_harness`` 自己做：它同时会看 web
        端口候选与桌面端单实例；在这里按 web 端口提前 return，会把用户选的
        桌面端/auto 目标一起挡掉（桌面端根本不上那个端口）。

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

        1. ``enable_chat``——旧口径；无 Chat 的变体连 Harness 菜单都不显示；
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
        if not self.enable_chat:
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
        读方是 DshMonitor 自身）。AppShell 侧只剩灵动岛的 agent 活跃指示——
        thinking/attention 注入联动管线与 offline 收口常驻气泡都在管理器内部完成。
        """
        island = getattr(self, "island", None)
        if island is not None and shiboken6.isValid(island):
            island.set_agent_active(to_state in ("working", "thinking"))

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
        from .chat import session_store as _session_store
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
        # 岛对话气泡（进程级）：提交会话并关闭——每条消息本就即时落盘，
        # 这里只兜底保存飞行中的流式回复（口径同各窗 quick_chat 的退出保存）
        bubble = getattr(self, "island_chat", None)
        if bubble is not None:
            _island_session = getattr(bubble, "session", None)
            _island_store = getattr(bubble, "store", None)
            if _island_session is not None and _island_store is not None:
                try:
                    _island_store.save(_island_session)
                except Exception:
                    logging.exception("退出前保存灵动岛对话会话失败")
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
            if not _session_store.close_all_writers(permanent=True):
                logging.warning("退出时会话写盘 worker 未干净关闭")
        except Exception:
            logging.exception("退出时关闭会话写盘 worker 失败")
        # 批5.2a：进程级共享子系统收口（agent_link / proactive / 全屏 watcher）。
        # 单窗关闭（退出这只）不触发——只有「全部退出」才停共享子系统。
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
        - 停灵动岛碰撞体定时器并注销全局聊天订阅（同生产收口口径）。

        不做 ``_on_about_to_quit`` 的退出语义（保存位置/永久关闭写盘 worker）：
        那是「全部退出」，测试收口不得触发。
        """
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
        try:
            shared.proxy.set_link_next_provider(shared.agent_link._next_busy_anim)
        except (AttributeError, RuntimeError):
            logging.exception("把窗口接入共享 Agent 联动链失败")

    def _sync_dynamic_island(self) -> None:
        """按配置创建/隐藏灵动岛；桌宠隐藏后灵动岛仍可常驻。"""
        island_cfg = self.config.get("dynamic_island", {})
        enabled = bool(island_cfg.get("enabled", True)) if isinstance(island_cfg, dict) else False
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
            from .dynamic_island import DynamicIsland

            self.island = DynamicIsland(self.config)
            # 纯桌宠版（无聊天模块）：hidden_chat 的岛单击路由回退为展开卡片，
            # 防"桌宠隐藏 → 岛点了没反应 → 无法恢复"的死锁。getattr 兼容
            # __new__ 测试桩（property 内 AttributeError 时取默认 True）
            self.island.set_chat_available(bool(getattr(self, "enable_chat", True)))
            # 岛图标默认取鱼本体头像（图片路径不碰 emoji 字体栈，见 dynamic_island
            # 的 _icon_pixmap 注释）；帧未就绪时岛侧只画底圈并稍后重试
            self.island.set_icon_provider(self._island_icon_pixmap)
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
        if body is None:
            from .island_collision import IslandCollisionBody

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

    def _island_chat_available(self) -> bool:
        """岛对话气泡可用：聊天功能在 + 岛存在 + hidden_chat 开。"""
        if not getattr(self, "enable_chat", True):
            return False
        island = getattr(self, "island", None)
        if island is None or not shiboken6.isValid(island):
            return False
        return self._island_hidden_chat_enabled()

    def _show_island_chat(self, *, activate: bool = True,
                          reply_text: str | None = None) -> None:
        """弹出锚定灵动岛的对话气泡（activate=False 为不抢焦点的预览弹出）。"""
        if not self._island_chat_available():
            return
        from .island_chat import IslandChatBubble

        if getattr(self, "island_chat", None) is None:
            self.island_chat = IslandChatBubble(self.config)
            self.island_chat.show_pet_requested.connect(self._show_pets_from_island_chat)
        bubble = self.island_chat
        bubble.open_chat_callback = self._open_full_chat_from_island_chat
        bubble.settings = self.config.chat_settings()
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
        False，调用方维持原丢弃行为。"""
        if self._aggregate_pet_visible() or not self._island_chat_available():
            return False
        island = getattr(self, "island", None)
        if island is None or not shiboken6.isValid(island):
            return False
        from .island_chat import IslandChatBubble

        if getattr(self, "island_chat", None) is None:
            self.island_chat = IslandChatBubble(self.config)
            self.island_chat.show_pet_requested.connect(self._show_pets_from_island_chat)
        bubble = self.island_chat
        bubble.open_chat_callback = self._open_full_chat_from_island_chat
        bubble.show_feedback(island, text, subtitle=subtitle, duration_ms=duration_ms)
        return True

    def _show_pets_from_island_chat(self) -> None:
        """岛对话气泡里的「显示桌宠」：恢复全部窗并同步岛状态。"""
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
            # 聚合可见态由唯一合成窗承担。
            return overlay.overlay.isVisible()
        return any(
            inst.win is not None and getattr(inst.win, "isVisible", lambda: True)()
            for inst in self._instances
        )

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

        pet.balance 按需导入：本函数只在"岛卡片展开 / 余额查询返回"时才被调用，
        启动路径（_sync_dynamic_island）不碰它（见 _show_balance_payload）。
        """
        from . import balance as balance_mod

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

    def _balance_worker(self, bridge, base_url: str, api_key: str, verify_ssl: bool, provider_key: str = '', *, quiet: bool = False) -> None:
        from . import balance as balance_mod  # 按需导入（见 _show_balance_payload）
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
        """
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
        slot_manager_mod.seed_slot_config_from_main(self.config.dir, slot_id)
        # 复用主窗同一配置根目录（AppShell.config.dir 的父目录），使所有窗的
        # config-slot-N.json / sessions-slot-N 落在同一 APP_DIR_NAME 下，仅按
        # instance_id 区分；显式传 instance_id，不再依赖进程级 DSH_PET_INSTANCE。
        new_config = Config(base=self.config.dir.parent, instance_id=instance_id)
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
        从集合移除；若退的是主窗则把列表头提升为新主窗（P1-3）；若为最后一窗
        则触发全部退出（app.quit）。进程级仅剩 ``close_all_writers(permanent=True)``
        只在「全部退出」（托盘退出 / _on_about_to_quit）收口。

        ``defer_heavy_teardown=True``（批 G，仅「退出子肥鱼」链式路径使用）：
        把有界但秒级的重资源回收——本窗 sessions writer 关闭（最多 2s join）、
        agent_link shutdown（最多 2s 共享 join）、碰撞会话停止（最多 3s+1s
        wait）——挪到进程级后台 reaper 线程串行执行；UI 线程只保留关窗、
        摘 runtime 标记、释放 slot 锁、从登记表移除等毫秒级必做步骤，
        N 只连清时主桌宠不再冻结。默认 False = 「退出这只」单窗路径保持
        既有同步语义逐位不变。重回收只做线程 join / queued 调用 / 纯 Python
        注册表操作，不触碰 Qt 对象，后台执行安全。
        """
        win = instance.win
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
        was_primary = instance is self.instance
        if instance in self._instances:
            self._instances.remove(instance)
        if was_primary:
            # P1-3：主窗退出后把列表头提升为新主窗（更新 self.instance），
            # 托盘/灵动岛/Dock 动作永远指向存活实例，防「复活」已退出的主窗。
            self.instance = self._instances[0] if self._instances else None
        self._refresh_tray_menu()
        if heavy_jobs:
            self._enqueue_heavy_teardown(instance, heavy_jobs)
        if not self._instances:
            # 最后一窗关闭 → 走全部退出语义（进程级 broker/碰撞/permanent writer 收口）
            self.app.quit()

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

    def _close_instance_subwindows(self, instance: PetInstance) -> None:
        """批5.2 P1-5：关闭本窗拥有的聊天窗/设置窗并断开引用。

        ChatWindow.closeEvent 只隐藏复用（widgets.py），因此还要显式隐藏 +
        调度 deleteLater + 清空实例引用，否则孤儿顶层窗在「退出这只」关掉本窗
        writer 之后仍可经 store 提交、复活写盘 worker（常驻到进程结束）。
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
                if not dialog.testAttribute(Qt.WidgetAttribute.WA_DeleteOnClose):
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
        from .todo_panel import TodoPanelDialog

        # Phase 1：即使总开关关闭，用户主动打开面板也需要服务对象（懒创建）。
        self._ensure_todo_service()
        if self.todo_panel is None:
            # parent 必须是 QWidget：overlay 拓扑下 self.win 是 sprite 壳（QObject），
            # 直接当 parent 会在 QDialog 构造期 TypeError。
            parent = self.win
            dialog = TodoPanelDialog(
                self, parent=parent if isinstance(parent, QWidget) else None)
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
        service = self._ensure_festival_service()
        service.remind_now()

    def toggle_festival_reminder(self) -> None:
        """「启用/关闭节日提醒」开关（默认隐藏、菜单编辑器可加回）：翻转配置并同步服务启停。"""
        self._toggle_flag("festival_reminder_enabled", self._sync_festival_service)

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
        menu.addAction('显示 / 隐藏', toggle_visible)
        menu.addAction('回到右下角', lambda: win.go_default_corner())

        island_action = menu.addAction('灵动岛')
        island_action.setCheckable(True)
        island_action.setChecked(bool(
            self.config.get("dynamic_island", {}).get("enabled", True)
        ))

        def toggle_island(enabled: bool) -> None:
            island_cfg = dict(self.config.get("dynamic_island", {}) or {})
            island_cfg["enabled"] = bool(enabled)
            self.config.set("dynamic_island", island_cfg)
            self.config.save()
            self._sync_dynamic_island()

        island_action.toggled.connect(toggle_island)

        if self.enable_chat:
            menu.addAction('AI 对话', self.instance.open_chat)
            menu.addAction('快速对话（气泡）', self.instance.open_quick_chat)
            menu.addAction('AI 设置', self.instance.open_chat_settings)
        menu.addAction('桌宠设置', self.instance.open_modern_settings)

        m_char = track_menu(menu.addMenu('切换角色'))
        current = str(self.config.get('character', catalog.DEFAULT_CHARACTER))
        for cid in catalog.list_available_characters():
            act = m_char.addAction(cid)
            act.setCheckable(True)
            act.setChecked(cid == current)
            act.triggered.connect(lambda checked=False, cid=cid: self.instance.switch_character(cid))

        mouse_through = menu.addAction('鼠标穿透')
        mouse_through.setCheckable(True)
        mouse_through.setChecked(bool(self.config.get('mouse_through', False)))
        mouse_through.toggled.connect(win.set_mouse_through)

        menu.addSeparator()

        auto = menu.addAction('开机自启')
        auto.setCheckable(True)
        auto.setChecked(autostart_mod.is_enabled())
        auto.toggled.connect(lambda enabled: self.instance._set_autostart(enabled, win))

        def sync_tray_checks() -> None:
            # 设置对话框/右键菜单里改过的开关，弹出托盘菜单前同步复选状态
            #（托盘菜单在 _build_tray 时一次性构建，不复用则不刷新会过期）
            mouse_through.setChecked(bool(self.config.get('mouse_through', False)))
            auto.setChecked(autostart_mod.is_enabled())
            island_action.setChecked(bool(
                self.config.get("dynamic_island", {}).get("enabled", True)
            ))

        menu.aboutToShow.connect(sync_tray_checks)

        menu.addSeparator()
        if self.enable_chat:
            menu.addAction('DeepSeek 余额', lambda: self.show_balance(win))
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

        menu.addAction('退出', self.app.quit)

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


def _mac_set_dock_icon_visible(visible: bool) -> None:
    """Switch the macOS application policy without restarting the pet.

    The speech bubble itself owns the non-activating window flags; application
    activation policy must not be used as a focus workaround because Accessory
    Regular (0) displays a Dock item; Accessory (1) keeps the application out
    of the Dock. Pet tool windows own their independent visibility/focus flags.
    """
    if sys.platform != 'darwin':
        return
    try:
        import ctypes
        import ctypes.util

        objc = ctypes.cdll.LoadLibrary(ctypes.util.find_library('objc') or '/usr/lib/libobjc.A.dylib')
        objc.sel_registerName.restype = ctypes.c_void_p
        objc.objc_getClass.restype = ctypes.c_void_p
        msg = objc.objc_msgSend
        msg.restype = ctypes.c_void_p
        msg.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        shared = msg(
            objc.objc_getClass(b'NSApplication'),
            objc.sel_registerName(b'sharedApplication'),
        )
        # NSApplicationActivationPolicyRegular = 0; Accessory = 1
        msg.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_long]
        msg(shared, objc.sel_registerName(b'setActivationPolicy:'), 0 if visible else 1)
    except Exception:
        pass


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
                if preferred_slot < 0 or preferred_slot > 127:
                    logging.error("无效的 --slot 参数 (必须在 0~127 范围内): %s", argv[index + 1])
                    return 1
            except ValueError:
                logging.error("无效的 --slot 参数: %s", argv[index + 1])
                return 1
        else:
            logging.error("缺少 --slot 参数值")
            return 1

    app = QApplication(argv)
    app.setApplicationName(APP_DIR_NAME)
    app.setQuitOnLastWindowClosed(False)

    # 确定配置根目录
    config_dir = _default_base() / APP_DIR_NAME

    # D4（PHASE4_DESIGN §4）overlay 拓扑单实例进程门：**先于** slot 竞争。
    # overlay 双开 = 两个进程各自恢复全部 sprite、位置回写互踩，第二实例必须在
    # 产生任何配置目录副作用（抢 slot-1、落种 config-slot-N.json）之前退场。
    # legacy 拓扑（PET_RENDER_TOPOLOGY 未设）下 acquire 返回 None，下面逐行不变。
    overlay_gate = overlay_gate_mod.acquire_overlay_instance_gate(config_dir)
    if overlay_gate is not None and not overlay_gate.acquired:
        # 双击桌面宠物场景：不弹窗、不阻塞，只留痕 + 退出。退出码对齐
        # pet/__main__.py 的 settings.lock 惯例（「已有实例，本次退出」= 0），
        # 免得外壳把"第二实例"当启动失败报错。
        logging.warning(
            "overlay 拓扑已有实例在运行（门 %s，持有者 pid=%s）：本次启动退出",
            overlay_gate.path, overlay_gate.holder_pid(),
        )
        return overlay_gate_mod.DUPLICATE_EXIT_CODE

    # 4.4a 停用多进程多宠退役层的**槽位文件锁**：main() 不再抢 slot-N.lock
    #（原语义 = 跨进程竞争「我是第几只」，退役层删除后不复存在）。`--slot`
    # 兼容解析保留（T5）：旧快捷方式/自启项仍按它选择 config-slot-N.json
    # 身份；缺省或 D4 门保证的单实例即主身份（slot 0）。
    slot_id = preferred_slot if preferred_slot is not None else 0

    try:
        instance_id = slot_manager_mod.slot_to_instance_id(slot_id)
        os.environ["DSH_PET_INSTANCE"] = instance_id

        # 迁移旧 spawn 实例（主槽或无并发运行旧实例时触发）
        if slot_id == 0:
            slot_manager_mod.migrate_legacy_spawns(config_dir)

        # 新 slot 落种：走共享落种函数。无存档 slot 按主设置落种；存在但未在该
        # 子肥鱼设置界面自定义过的 slot 按主设置刷新；已自定义（user_customized）
        # 的 slot 一个键都不碰。落种/刷新永不写位置键。
        slot_manager_mod.seed_slot_config_from_main(config_dir, slot_id)

        config = Config(instance_id=instance_id)
        _mac_set_dock_icon_visible(bool(config.get("show_dock_icon", True)))
        _setup_logging(config)

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
        # 才释放 overlay 进程门——比 _on_about_to_quit 内更晚，避免"本地还在收尾、
        # 第二实例已能起来"的位置回写竞态。会话结束路径另见 _on_session_end。
        if overlay_gate is not None:
            try:
                overlay_gate.release()
            except Exception:
                logging.exception("释放 overlay 进程门失败")


if __name__ == '__main__':
    sys.exit(main())
