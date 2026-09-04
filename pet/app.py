# -*- coding: utf-8 -*-
"""
应用入口 —— QApplication + 桌宠窗口 + 系统托盘。

支持运行时切换角色：
- 右键桌宠 →「切换角色」
- 托盘菜单 →「切换角色」
切换后会热加载对应形象的 webm，并保留位置/朝向等配置。

批5.1（纯重构）把原 PetApp 按「进程级 / 每窗」拆成两块，行为逐位不变、
运行时仍一进程一窗：
- ``AppShell``：进程级服务（托盘、灵动岛、dock 菜单、更新、余额、系统通知、
  碰撞会话、broker、aboutToQuit 收口），并持有唯一的 ``PetInstance``。
- ``PetInstance``：每窗容器（config/lib/win/聊天窗/设置窗/气泡），持 backref
  到 ``AppShell``，窗口级操作都从这里路由，``self.win`` 单窗假设据此收敛。
"""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

import shiboken6
from PySide6.QtCore import QObject, QTimer, Qt, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QSystemTrayIcon

from . import autostart as autostart_mod
from . import balance as balance_mod
from . import catalog
from . import click_sound
from . import slot_manager as slot_manager_mod
from . import updater
from . import webm_clip as webm_clip_mod
from .config import APP_DIR_NAME, Config, _default_base
from .context_menus.shared import open_deepseek_web
from .desktop_notify import DesktopNotification, position_stack
from .harness_launcher import launch_harness_gui
from .instance_launcher import launch_new_pet
from .library import MovieLibrary
from .window import PetWindow
from .fun_image_popup import restore_ojingjing_windows
from .runtime_cleanup import cleanup_stale_runtime_dirs
from .collision_ipc import CollisionIpcSession
from .decode_broker import BrokerFacade


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


def _show_balance_payload(win, payload) -> None:
    """展示余额气泡（含峰谷副标题）并按余额档位触发余额动画。

    网络查询、内存缓存、文件缓存三条路径统一走这里，避免缓存命中时
    没有副标题/不播动画导致的行为不一致。
    """
    if win is None or not shiboken6.isValid(win):
        return
    if isinstance(payload, dict):
        text = str(payload.get("text") or "余额信息为空")
        info = payload.get("info") or {}
    else:
        text = str(payload)
        info = {}
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


class _UpdateBridge(_BackgroundResult):
    def __init__(self, parent):
        super().__init__()
        self.parent = parent
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
                "可从“更新与帮助”打开项目页下载。",
                duration_ms=9000,
            )


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

    flag 关（生小肥鱼走独立进程）时 instance_launcher 写该 env、子进程 main()
    启动须把它接到主窗的 spawn_offset 构造参数——否则 _apply_spawn_offset 的
    index 恒为 0，新孵化的桌宠直接与母桌宠完全重叠（flag 关行为回归）。
    """
    try:
        return max(0, int(os.environ.get('DSH_PET_SPAWN_OFFSET_INDEX', '0') or '0'))
    except (TypeError, ValueError):
        return 0


class PetInstance:
    """每窗容器 —— config/lib/win/聊天窗/设置窗/气泡与全部窗口级操作。

    批5.1（纯重构）拆自原 PetApp 的「每窗」半边，行为逐位不变，运行时仍
    一进程一窗。持 backref 到所属 ``AppShell``；窗口级逻辑集中于本类后，
    ``self.win`` 单窗假设只有一个归属点，便于批5.2 扩成多窗集合。
    """

    def __init__(self, shell: "AppShell", config: Config, enable_chat: bool = True,
                 slot_handle=None, slot_id: int | None = None,
                 spawn_offset: int = 0) -> None:
        self.shell: AppShell = shell
        self.config = config
        self.slot_handle = slot_handle
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
        # 批5.2 P1-1：碰撞会话/broker 移回**本窗**自持（每窗一个，经
        # collision_ipc._local_election_names 同进程收敛成「一协调者 + N 客户端」），
        # 每窗 runtime_id 由各自 instance_id 派生 → 两窗互撞与多进程双开等价；
        # 「退出这只」只停本窗，switch_character 只重建本窗（C2 地雷随之消解）。
        self.collision_ipc = CollisionIpcSession(config, self.shell)
        self.broker_facade = BrokerFacade(
            enabled=bool(config.get('decode_broker_enabled', False)))

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
        # 预热策略：默认 balanced（高频交互链预热首帧，随机动作池只取元数据
        # 按需解码）；省电模式（闲置降帧开关）强制 minimal（连高频链也不预热，
        # 常驻内存最低）。media_prewarm 配置键保留给手改 config 的高级用户
        # （可显式设 full），但被省电模式覆盖。
        prewarm = str(self.config.get("media_prewarm", "balanced") or "balanced")
        if self.config.get("idle_low_fps_enabled", False):
            prewarm = "minimal"
        # 首帧缓存全局预算（高级用户可在 config.json 调小，省电/低配机用）；
        # 进程级设置，幂等，切角色重复调用无害。
        webm_clip_mod.set_first_frame_budget(
            int(self.config.get("first_frame_cache_max_mb", 32)) * 1024 * 1024
        )
        lib = MovieLibrary(
            character_id=character_id,
            prewarm_policy=prewarm,
        )
        # UI 就绪后统一调度预热：高优先级立即后台跑（带 0~0.5s 错峰），
        # 随机动作池延迟 2s 补全，避免多开启动时 ffmpeg 进程洪峰。
        lib.schedule_high_priority_warm()
        lib.schedule_low_priority_warm()
        logging.info('素材加载完成：%s %d 段动画', character_id, len(lib.names()))
        return lib

    def _slot_wrap(self, fn):
        """包一层：调用回调时把线程本地日志槽位设为本窗 slot（批5.2 §③.7）。

        flag 关（单窗）时槽位为 None，日志格式与现状逐位一致；flag 开/多窗
        时槽位为 'slot-N'，由 _SlotLogFilter 加 [slot-N] 前缀。
        """
        if fn is None:
            return None
        slot = f"slot-{self.slot_id}" if self.slot_id is not None else "slot-0"

        def wrapper(*args, **kwargs):
            prev = getattr(_pet_log_slot, "slot", None)
            # P1-2：窗级逻辑一律读进程级 flag 快照（shell._single_process_spawn），
            # 不读每窗 config（第二窗 config-slot-N 里该键无效）。
            if self.shell._single_process_spawn:
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
        win.on_spawn_pet = self._slot_wrap(self.shell.spawn_pet)
        win.on_restore_fun_windows = restore_ojingjing_windows
        win.on_hidden = self._slot_wrap(self._notify_pet_hidden)
        # 批5.2 P0-2：右键「退出」注入窗级「退出这只」只在 flag 开（多窗）时；
        # flag 关（单窗）不注入 → _request_quit 走旧 app.quit 分支，逐位一致。
        if self.shell._single_process_spawn:
            win.on_exit_window = self._slot_wrap(self._request_exit_window)
        else:
            win.on_exit_window = None

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
        win = PetWindow(lib, self.config, collision_session=self.collision_ipc,
                        broker_facade=self.broker_facade,
                        single_process_spawn=self.shell._single_process_spawn)
        # P1-2：窗级 runtime 标记版本化 / 日志前缀读进程级 flag 快照（不读每窗 config）。
        # N-1：快照经构造参数在 _restore_position 之前生效（窗构造期就会写标记）。
        self._wire_window(win)
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

        # 批5.2 P1-1：碰撞会话/broker 已移回**本窗**自持（每窗一个）——热切换
        # 只重建本窗的 session/facade（detach 旧窗 client → 停/重建本窗会话 →
        # 新窗 attach 到新会话）。C2 前向地雷（多窗下任一窗热切换拆共享进程级
        # IPC/broker）随「不再共享」自然消解。
        old_win = self.win
        old_win.detach_collision_session()
        # 停本窗旧会话/facade 并重建（影响仅限本窗，不碰其它窗）
        try:
            self.collision_ipc.stop()
        except Exception:
            logging.exception("切换角色：停止本窗碰撞会话失败")
        try:
            self.broker_facade.shutdown()
        except Exception:
            logging.exception("切换角色：关闭本窗 broker 失败")
        self.collision_ipc = CollisionIpcSession(self.config, self.shell)
        self.broker_facade = BrokerFacade(
            enabled=bool(self.config.get('decode_broker_enabled', False)))
        self.collision_ipc.start()
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
        x = self.win.x() + horizontal * 48 * index
        y = self.win.y() + vertical * 32 * index
        # 小屏（可用区比窗口还窄/矮）时上界 < 下界，min/max 会互相打架把
        # 窗口推出屏幕外；先判边界再钳制。
        max_x = available.right() - self.win.width() + 1
        max_y = available.bottom() - self.win.height() + 1
        x = available.left() if max_x < available.left() else min(max(x, available.left()), max_x)
        y = available.top() if max_y < available.top() else min(max(y, available.top()), max_y)
        self.win.move(x, y)

    # ------------------------------------------------------------ 聊天窗
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
            self.quick_chat = QuickChatBubble(self.config, pet_window=self.win)
            self.quick_chat.open_chat_callback = self.open_chat
        else:
            self.quick_chat.pet_window = self.win
            self.quick_chat.settings = self.config.chat_settings()
            self.quick_chat.refresh_session()
        self.quick_chat.show_for_pet(self.win)

    def open_legacy_chat(self) -> None:
        if not self.enable_chat or self.win is None:
            return
        if self._defer_while_popup_active("legacy-chat", self.open_chat):
            return
        from .chat.legacy_widgets import ChatWindow
        if self.legacy_chat_window is None:
            self.legacy_chat_window = ChatWindow(
                self.config,
                str(self.config.get('character', catalog.DEFAULT_CHARACTER)),
                pet_window=self.win,
                notifier=self.shell.system_notify,
                auth_callback=self.open_chat_settings,
            )
        else:
            self.legacy_chat_window.set_pet_window(self.win)
        self.chat_window = self.legacy_chat_window
        self._present_dialog(self.legacy_chat_window, lambda: self.legacy_chat_window.position_near_pet(self.win))

    def open_modern_chat(self) -> None:
        if not self.enable_chat or self.win is None:
            return
        if self._defer_while_popup_active("modern-chat", self.open_modern_chat):
            return
        from .chat.widgets import ChatWindow
        if self.modern_chat_window is None:
            self.modern_chat_window = ChatWindow(
                self.config,
                str(self.config.get('character', catalog.DEFAULT_CHARACTER)),
                pet_window=self.win,
                notifier=self.shell.system_notify,
                auth_callback=self.open_chat_settings,
            )
        else:
            self.modern_chat_window.set_pet_window(self.win)
        self.chat_window = self.modern_chat_window
        self._present_dialog(self.modern_chat_window, lambda: self.modern_chat_window.position_near_pet(self.win))

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
        """任一设置窗口打开时暂停桌宠气泡，避免气泡盖住设置界面。"""
        if getattr(self, "win", None) is None:
            return
        any_open = (
            getattr(self, "modern_settings_dialog", None) is not None
            or getattr(self, "chat_settings_dialog", None) is not None
        )
        self.win.set_bubble_suppressed(any_open)

    def open_modern_settings(self) -> None:
        from .modern_settings_dialog import ModernSettingsDialog
        if self.modern_settings_dialog is None:
            dialog = ModernSettingsDialog(
                self.config,
                self.win,
                include_ai=self.enable_chat,
            )
            dialog.finished.connect(self._modern_settings_finished)
            self.modern_settings_dialog = dialog
        self._update_bubble_suppression_for_settings()
        # 在 show 之前定位，避免 Windows 上窗口先显示默认位置再跳走（闪现小窗）
        self._present_dialog(
            self.modern_settings_dialog,
            before_present=self.modern_settings_dialog.move_away_from_pet,
        )

    def _modern_settings_finished(self, result: int) -> None:
        self.modern_settings_dialog = None
        self._update_bubble_suppression_for_settings()
        # 新版设置在关闭时一律落盘（closeEvent 自动保存，「保存并退出」同样走
        # _write_config），因此无论 Accepted/Rejected 都把改动应用到桌宠。
        # 此前只有 Accepted 才刷新：直接 X 关闭时保存生效但桌宠不更新。
        if self.win is not None:
            self.win.refresh_pet_settings()
        self.shell._sync_dynamic_island()
        self.shell._apply_balance_timer()
        self._refresh_chat_windows()
        _mac_set_dock_icon_visible(bool(self.config.get("show_dock_icon", True)))

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
        """用户主动隐藏桌宠后弹托盘提示，指明恢复入口。"""
        if getattr(self.shell, "island", None) is not None:
            self.shell.island.set_pet_visible(False)
        if self.shell.tray is None:
            return
        self.shell.tray.showMessage(
            "桌宠已隐藏",
            "点击托盘图标或 Dock 图标即可恢复。",
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
                 slot_handle=None, slot_id: int | None = None,
                 spawn_offset: int = 0) -> None:
        self.app = app
        self.config = config
        self._enable_chat = bool(enable_chat)
        self._slot_id = slot_id
        self.tray: QSystemTrayIcon | None = None
        self.dock_menu: QMenu | None = None
        self._notification_click_callback = None
        self._toast_windows: list[DesktopNotification] = []
        self.island = None
        self._spawned_pet_count = 0
        self._balance_busy = False
        self._balance_cache = None
        self._balance_bridge = None
        self._on_about_to_quit_connected = False
        self._balance_timer = QTimer()
        self._balance_timer.timeout.connect(self.show_balance)
        self._update_bridge = None
        self._balance_cache_path = config.dir / 'balance_cache.json'  # 跨实例共享余额缓存（按 provider 绑定）
        # 批5.2 P1-2/P2-6：进程级 flag 快照——启动期从主窗 config 读一次存
        # _single_process_spawn；窗级逻辑（runtime 标记版本化、日志前缀、
        # 退出分派、spawn 分发）一律读本快照，不读每窗 config。第二窗的
        # config-slot-N.json 里该键不再有任何作用，运行期手改 config.json
        # 翻 flag 也因此失效（需重启）。
        self._single_process_spawn = bool(config.get('experimental_single_process_spawn', False))
        # 批5.2 P1-1：碰撞会话/broker 移回各 PetInstance 自持（不再由 AppShell 持有）；
        # 每窗一个，经 collision_ipc._local_election_names 同进程收敛。
        # 每窗容器：批5.1 单进程单窗仅一个；批5.2 spike 扩成多窗集合
        #（self.instance 指主窗 = instances[0]，兼容既有调用面）。
        self._instances: list[PetInstance] = []
        self.instance = PetInstance(
            self, config, enable_chat=self.enable_chat, slot_handle=slot_handle,
            slot_id=slot_id, spawn_offset=spawn_offset,
        )
        self._instances.append(self.instance)

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

    # ------------------------------------------------------------ 启动
    def start(self) -> None:
        # aboutToQuit 只在控制器层绑定一次：角色热切换会重建窗口，逐个
        # connect win.save_position 会在旧窗口延迟销毁后残留失效引用。
        # 统一走 _on_about_to_quit，在信号触发时读取当前有效窗口。
        if not self._on_about_to_quit_connected:
            self.app.aboutToQuit.connect(self._on_about_to_quit)
            self._on_about_to_quit_connected = True
        self.instance.collision_ipc.start()
        character_id = str(self.config.get('character', catalog.DEFAULT_CHARACTER))
        logging.info('当前形象: %s', character_id)
        self.instance._create_ui(character_id)
        self._install_macos_dock_menu()
        self._sync_dynamic_island()
        self.instance._apply_spawn_offset()
        self._apply_balance_timer()
        QTimer.singleShot(3500, self.instance._check_autostart_wanted)

    # ------------------------------------------------------------ 退出收口
    def _on_about_to_quit(self) -> None:
        """退出前保存各窗位置并释放资源（全进程「全部退出」语义，R5 切分）。

        aboutToQuit 只绑定一次自本控制器；切换角色会重建桌宠窗口，信号
        触发时读取当前窗口（经 ``self.instance.win``），避免调用已延迟销毁
        的旧窗口。

        R5 切分：**窗级**项（位置/预热/Agent/各窗会话保存/slot 锁）逐窗收口；
        批5.2 P1-1 后 broker/碰撞会话也移回**每窗**自持，故与窗级一起逐窗停；
        **进程级**仅剩 ``close_all_writers(permanent=True)`` 只停一次——多窗下
        任一窗退出不许停进程级资源，只有「全部退出」才收口（这也是「退出这只」
        与「全部退出」的核心差异）。
        """
        from .chat import session_store as _session_store
        # 窗级收口：逐窗保存位置、停本窗预热与 Agent、提交本窗会话、释放本窗 slot 锁
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
            if inst.slot_handle is not None:
                try:
                    slot_manager_mod._unlock_file(inst.slot_handle)
                except Exception:
                    pass
                inst.slot_handle = None
            # 批5.2 P1-1：每窗自持碰撞会话与 broker，逐窗收口（「全部退出」逐窗停）
            try:
                inst.broker_facade.shutdown()
            except Exception:
                logging.exception("退出时关闭 broker facade 失败")
            try:
                inst.collision_ipc.stop()
            except Exception:
                logging.exception("退出时停止碰撞会话失败")
        # 会话异步写盘（B8）：全部会话已保存，再永久关闭写盘 worker
        #（关掉后迟到的 queued 回调提交会被明确拒绝）。
        try:
            if not _session_store.close_all_writers(permanent=True):
                logging.warning("退出时会话写盘 worker 未干净关闭")
        except Exception:
            logging.exception("退出时关闭会话写盘 worker 失败")

    def _sync_dynamic_island(self) -> None:
        """按配置创建/隐藏灵动岛；桌宠隐藏后灵动岛仍可常驻。"""
        island_cfg = self.config.get("dynamic_island", {})
        enabled = bool(island_cfg.get("enabled", False)) if isinstance(island_cfg, dict) else False
        if not enabled:
            if getattr(self, "island", None) is not None:
                self.island.hide()
            return
        if getattr(self, "island", None) is None:
            from .dynamic_island import DynamicIsland

            self.island = DynamicIsland(self.config)
            self.island.clicked.connect(self._toggle_pet_from_island)
        self.island.refresh_from_config()
        pet_visible = True
        win = self.instance.win
        if win is not None:
            is_visible = getattr(win, "isVisible", None)
            pet_visible = bool(is_visible()) if callable(is_visible) else True
        self.island.set_pet_visible(pet_visible)
        self.island.show()

    def _toggle_pet_from_island(self) -> None:
        if self.instance is None:  # N-7：末窗退出后 quit 前的防御性守卫
            return
        win = self.instance.win
        if win is None:
            return
        if win.isVisible():
            win.hide(notify=False)
        else:
            win.show()
        if getattr(self, "island", None) is not None:
            self.island.set_pet_visible(win.isVisible())

    def _apply_balance_timer(self) -> None:
        self._balance_timer.stop()
        minutes = max(0, int(self.config.get("balance_refresh_minutes", 0) or 0))
        if minutes:
            self._balance_timer.start(minutes * 60000)

    def _update_island_balance(self, payload) -> None:
        """把余额文本/峰谷提示同步给灵动岛（若有）。"""
        if getattr(self, "island", None) is None:
            return
        text = "余额 --"
        info = {}
        if isinstance(payload, dict):
            text = str(payload.get("text") or "余额 --")
            info = payload.get("info") or {}
        peak_label, idle_label = balance_mod.resolve_tier_labels(
            str(self.config.get("balance_tier_labels_mode", "default") or "default"),
            str(self.config.get("balance_tier_label_peak", "") or ""),
            str(self.config.get("balance_tier_label_idle", "") or ""),
        )
        hint = balance_mod.deepseek_pricing_hint(
            peak_label=peak_label, idle_label=idle_label,
        )
        self.island.set_balance_info(hint, text)

    # ------------------------------------------------------------ 余额
    def show_balance(self, parent=None) -> None:
        win = parent or (self.instance.win if self.instance is not None else None)
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
        threading.Thread(
            target=self._balance_worker,
            args=(bridge, provider.base_url, provider.api_key, provider.verify_ssl, provider_key),
            daemon=True, name='pet-balance',
        ).start()

    def _balance_worker(self, bridge, base_url: str, api_key: str, verify_ssl: bool, provider_key: str = '') -> None:
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
        bridge = _UpdateBridge(target)
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
        """按 feature flag 决定是 spawn 新进程还是进程内建第二个 PetInstance。

        flag ``experimental_single_process_spawn`` 默认关 = 走 ``launch_new_pet``
        独立进程路径（行为与现状逐位一致）。开 = 进程内创建新窗（批5.2 spike）。
        """
        if not self._single_process_spawn:
            try:
                self._spawned_pet_count += 1
                launch_new_pet(self._spawned_pet_count)
            except OSError as exc:
                self._spawned_pet_count = max(0, self._spawned_pet_count - 1)
                logging.exception('生小肥鱼失败')
                _show_startup_error('生小肥鱼失败', str(exc))
            return
        try:
            self._spawned_pet_count += 1
            self.spawn_in_process_window(self._spawned_pet_count)
        except Exception as exc:
            self._spawned_pet_count = max(0, self._spawned_pet_count - 1)
            logging.exception('进程内生成小肥鱼失败')
            _show_startup_error('生小肥鱼失败', str(exc))

    def spawn_in_process_window(self, offset_index: int = 1) -> PetInstance:
        """批5.2 spike：进程内创建第二个 PetInstance（不共享库/Config/SessionStore）。

        - 新 slot 身份：经 slot_manager 抢占下一个空闲 slot（单进程内 slot 语义
          从「进程互斥」变「窗身份分配」，文件锁释放语义不变）；
        - 独立 Config(instance_id=slot-N)，显式传 instance_id（不再依赖进程级
          DSH_PET_INSTANCE），独立 MovieLibrary / PetWindow / SessionStore 目录；
        - 碰撞仍走 QLocal 回环：新窗 attach 到**本窗自持**的 collision_ipc（每窗一个，
          P1-1），runtime_id 由自身 instance_id 派生，同进程多 session 经
          `_local_election_names` 收敛成「一协调者 + N 客户端」。
        """
        config_dir = self.config.dir
        # 单进程内 slot 语义 = 窗身份分配：不能再用跨进程文件锁做同进程竞争
        #（同一进程可再次锁住已持有的 slot-N 锁，导致两窗撞同一 slot）。先
        # 收集本进程已占用的 slot_id，再逐位申请未被占用且未被它进程持有的。
        used = {inst.slot_id for inst in self._instances}
        slot_id = None
        slot_handle = None
        candidate = 0
        # P2-1：slot 扫描加上限（max_scan_slots=128）。超限/持续失败抛
        # SlotManagerError，由 spawn_pet 的 except 捕获走 _show_startup_error，
        # 不许无限循环（持续 IO 失败 / 128 个槽全部被占时）。
        while candidate < 128:
            if candidate not in used:
                try:
                    slot_id, slot_handle = slot_manager_mod.acquire_pet_slot(
                        config_dir, preferred_slot=candidate)
                    break
                except slot_manager_mod.SlotLockError:
                    pass
            candidate += 1
        else:
            raise slot_manager_mod.SlotManagerError(
                "进程内生小肥鱼：前 128 个槽位均被占用或无法获取锁")
        instance_id = slot_manager_mod.slot_to_instance_id(slot_id)
        # 复用主窗同一配置根目录（AppShell.config.dir 的父目录），使所有窗的
        # config-slot-N.json / sessions-slot-N 落在同一 APP_DIR_NAME 下，仅按
        # instance_id 区分；显式传 instance_id，不再依赖进程级 DSH_PET_INSTANCE。
        new_config = Config(base=self.config.dir.parent, instance_id=instance_id)
        character_id = str(new_config.get('character', catalog.DEFAULT_CHARACTER))
        inst = PetInstance(
            self, new_config, enable_chat=self.enable_chat,
            slot_handle=slot_handle, slot_id=slot_id, spawn_offset=offset_index,
        )
        # 批5.2 P1-6：spike 声明「进程内多窗 × decode_broker_enabled」组合不支持
        #（共享 facade 的跨窗干扰，见 REVIEW_batch52 P1-6）——主窗/新窗 config
        # 里 broker 为真时记 warning 并停用新窗 broker（不 bind），批5.3 共享
        # 解码链落地后移除本限制。
        if self.config.get('decode_broker_enabled', False) or new_config.get('decode_broker_enabled', False):
            logging.warning(
                "进程内多窗与 decode_broker_enabled 组合在 spike 阶段不支持："
                "新窗 %s 停用 broker（不 bind），批5.3 前有效", instance_id)
            inst.broker_facade = BrokerFacade(enabled=False)
        # P1-1：每窗自持碰撞会话需先 start，新窗 attach 才走 QLocal 收敛。
        inst.collision_ipc.start()
        # build_tray=False：非主窗不再新建/替换进程级托盘，改由 _refresh_tray_menu 聚合。
        inst._build_window(character_id, build_tray=False)
        self._instances.append(inst)
        inst._apply_spawn_offset()
        self._refresh_tray_menu()
        # N-5：msg 不手写 [slot-N] 前缀——_slot_wrap/_SlotLogFilter 会加调用方
        # 槽位前缀，叠加成双前缀纯噪音；新窗身份保留在正文里。
        logging.info("进程内新窗已创建 (slot=%s, instance=%s)", slot_id, instance_id)
        return inst

    def _on_window_exit_requested(self, instance: PetInstance) -> None:
        """窗级「退出这只」（R5 切分）：只收口本窗，不碰其它窗的进程级资源。

        顺序：存本窗位置 → 停本窗预热/Agent → 保存本窗三聊天窗 live session
        （P0-2，对齐 aboutToQuit 安全网）→ 关闭/断开本窗从属窗（P1-5，防 writer
        复活）→ 删本窗 runtime 标记 → 关本窗 sessions writer（非 permanent，
        2s 超时）→ 释放本窗 slot 锁 → 停本窗碰撞会话/broker（P1-1）→ 关本窗 →
        从集合移除；若退的是主窗则把列表头提升为新主窗（P1-3）；若为最后一窗
        则触发全部退出（app.quit）。进程级仅剩 ``close_all_writers(permanent=True)``
        只在「全部退出」（托盘退出 / _on_about_to_quit）收口。
        """
        win = instance.win
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
            if getattr(win, 'agent_link_manager', None) is not None:
                try:
                    win.agent_link_manager.shutdown()
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
        self._close_instance_session_writer(instance)
        if instance.slot_handle is not None:
            try:
                slot_manager_mod._unlock_file(instance.slot_handle)
            except Exception:
                pass
            instance.slot_handle = None
        # 批5.2 P1-1：停本窗自持的碰撞会话与 broker（只影响本窗）。
        try:
            instance.collision_ipc.stop()
        except Exception:
            logging.exception("退出这只：停止碰撞会话失败")
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
        if not self._instances:
            # 最后一窗关闭 → 走全部退出语义（进程级 broker/碰撞/permanent writer 收口）
            self.app.quit()

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
            self.island.set_pet_visible(win.isVisible())

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

    def _build_tray(self, win: PetWindow, tray: QSystemTrayIcon | None = None) -> QSystemTrayIcon:
        # 批5.2：可复用已有托盘（_refresh_tray_menu 传 self.tray），避免多窗各自
        # 建托盘图标；新建时绑定双击切换，复用时不重复连接（activated 只接一次）。
        if tray is None:
            tray = QSystemTrayIcon(QIcon(win.icon_pixmap()))
            tray.activated.connect(
                lambda reason: self._toggle_primary_pet_visible()
                if reason == QSystemTrayIcon.ActivationReason.DoubleClick
                else None
            )

        def toggle_visible() -> None:
            if win.isVisible():
                win.hide()
            else:
                win.show()
            if getattr(self, "island", None) is not None:
                self.island.set_pet_visible(win.isVisible())

        menu = QMenu()
        # 气泡是置顶 Tool 窗口（层级高于原生菜单 popup），托盘菜单弹出前
        # 先隐藏气泡，避免气泡盖住菜单
        menu.aboutToShow.connect(lambda: win.hide_speech_bubble())
        menu.addAction('显示 / 隐藏', toggle_visible)

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

        m_char = menu.addMenu('切换角色')
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
            menu.addAction('启动 DeepSeek Harness', lambda: launch_harness_gui(win))
        else:
            # 纯桌宠版本不提供本地 DSH 启动入口，只保留网页版入口
            menu.addAction('打开网页版 DeepSeek', open_deepseek_web)
        menu.addAction('检查更新', lambda: self.check_update(win))

        # 批5.2 spike：多窗时在底部聚合「每窗：显示/隐藏、退出这只」（聚合美化留 5.2a）。
        if len(self.instances) > 1:
            menu.addSeparator()
            for inst in self.instances:
                win_i = inst.win
                if win_i is None:
                    continue
                slot_label = f"[slot-{inst.slot_id}]" if inst.slot_id is not None else ""

                def _toggle(win=win_i) -> None:
                    if win.isVisible():
                        win.hide()
                    else:
                        win.show()

                def _exit(inst=inst) -> None:
                    self._on_window_exit_requested(inst)

                menu.addAction(f'显示 / 隐藏 {slot_label}', _toggle)
                menu.addAction(f'退出这只 {slot_label}', _exit)

        menu.addAction('退出', self.app.quit)

        tray.setContextMenu(menu)
        tray.setToolTip('dsh-pet 独立桌宠')
        tray.show()
        return tray


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


def main(argv: list[str] | None = None, enable_chat: bool = True) -> int:
    _default_xcb_platform_on_wayland()
    argv = list(argv if argv is not None else sys.argv)
    preferred_slot = None

    if "--instance" in argv:
        logging.error("参数 --instance 已弃用并移除，多开实例请改用 --slot <0-127>")
        return 1

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

    # 执行槽位竞争取得排他锁
    slot_handle = None
    slot_id = None

    try:
        try:
            slot_id, slot_handle = slot_manager_mod.acquire_pet_slot(config_dir, preferred_slot=preferred_slot)
        except Exception as exc:
            logging.exception("获取桌宠槽位锁失败")
            _show_startup_error("dsh-pet-standalone", str(exc))
            return 1

        instance_id = slot_manager_mod.slot_to_instance_id(slot_id)
        os.environ["DSH_PET_INSTANCE"] = instance_id

        # 迁移旧 spawn 实例（主槽或无并发运行旧实例时触发）
        if slot_id == 0:
            slot_manager_mod.migrate_legacy_spawns(config_dir)

        config = Config(instance_id=instance_id)
        _mac_set_dock_icon_visible(bool(config.get("show_dock_icon", True)))
        _setup_logging(config)

        logging.info("dsh-pet-standalone 启动 (slot: %s, instance: %s)", slot_id, instance_id)
        _cleanup_stale_runtime_dirs()
        stale_removed = autostart_mod.cleanup_stale_entries()
        if stale_removed:
            logging.info("已清理 %d 个指向不存在路径的开机自启项", stale_removed)

        controller = AppShell(app, config, enable_chat=enable_chat, slot_handle=slot_handle, slot_id=slot_id,
                              spawn_offset=_read_spawn_offset_env())
        try:
            controller.start()
        except Exception as exc:
            logging.exception("启动失败")
            _show_startup_error("dsh-pet-standalone", str(exc))
            return 1

        logging.info("进入事件循环")
        return app.exec()
    finally:
        if slot_handle is not None:
            try:
                slot_manager_mod._unlock_file(slot_handle)
            except Exception:
                pass
            slot_handle = None


if __name__ == '__main__':
    sys.exit(main())
