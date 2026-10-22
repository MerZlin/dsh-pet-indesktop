# -*- coding: utf-8 -*-
"""SpriteMenuFacade：context_menus 建造器的 pet 形适配面。

``context_menus.modern.build_modern_menu``（Menu Action Model：注册表 + 用户
编排 + 图标/分组）以 PetWindow 的方法面为输入；本类把这套面映射到 OverlayShell
+ 被点 sprite + 行为控制器，使**同一个建造器原样复用**——菜单结构只写一次
（在 context_menus 里），窗口路径与 overlay 路径语义逐点一致，overlay 不再自带
一份会漂移的硬编码布局。旧版扁平布局及它的模板分发已于 2026-10-08 全量退役
（见 docs/PR-REPORT-LEGACY-MENU-RETIREMENT-2026-10-08.md）。

样式安装与 ``pet/context_menu.py::populate_context_menu`` 逐条对齐（现代外观 /
勾选指示 / 响应式样式 / 停留交互），两条路径出的菜单因此长得一样。

路由表（窗口侧方法面 → overlay 落点，逐项等价说明见各方法 docstring）：

    cfg/scale/change_scale/playback_speed/set_playback_speed  → 被点 sprite + 各自那份 config
    on_open_chat / on_open_chat_settings                      → PetInstance.open_chat*
    on_open_modern_settings                                   → OverlayShell.open_settings_for
    on_show_balance / on_check_update / on_open_todo_panel /
    on_voice_chime_* / on_festival_*                          → AppShell（进程级服务）
    toggle_agent_link                                         → agent_link_manager（共享 manager）
    toggle_proactive_enabled / set_proactive_option           → proactive_watcher（共享监视器）
    trigger_golden_spin / look_at_screen / rename_character   → 壳的 sprite 等价实现（作用被点 sprite）
    install_music_lyric / _music_lyric                        → 壳的音乐（歌词）宿主
    set_edge_probe_enabled                                    → config（探头世界每 tick 热读）

4.2c D13：菜单作用对象 = 右键命中的那一只（``sprite`` 参数）。设置入口按它
自己的 config 身份传 ``--instance``（子肥鱼 = slot-N），「退出这只」接
``OverlayShell.exit_pet(sprite)``；主子同样保档案、留空、可恢复，不提升不quit。

批 B2：作用对象口径对齐旧版一窗一宠（菜单以 ``pet=self`` 建造，每条都作用
本窗那一只）——读（勾选态/素材清单）与写（动画/速率/大小/拖动物理/回角落/
黄金回旋/隐藏/看屏幕）一律取 ``self.sprite``，不再混用 ``self._shell.sprite``。
批 B7b：逐只设置的持久化与读取都按该 sprite 自己的配置走——``cfg`` 是它的
配置视图（子宠 = ``config-slot-N.json``，缺键取同源默认），``_persist_sprite_setting``
写它自己的那份（主宠仍写主配置），「切换角色」也只切被点的那一只。
"""
from __future__ import annotations

import logging

from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import QInputDialog, QMenu

from . import catalog
from .context_menus import build_modern_menu
from .context_menus.menu_styles import (
    apply_modern_menu_style,
    install_modern_check_indicators,
)
from .context_menus.menu_styles.common import (
    install_stay_open_interaction,
)
from .context_menus.shared import add_action as _add_action
from .context_menus.shared import (
    animation_icon_cache_entry,
    animation_icon_cache_hit,
    animation_icon_cache_key,
)

log = logging.getLogger(__name__)


def _first_callable(hosts, names):
    """按宿主优先级取第一个可调用属性（缺失/非可调用一律跳过，不抛）。"""
    for host in hosts:
        if host is None:
            continue
        for name in names:
            fn = getattr(host, name, None)
            if callable(fn):
                return fn
    return None


class SpriteMenuFacade:
    """context_menus 建造器的 pet 形适配面（duck-typed，非 PetWindow 子类）。

    ``sprite`` = 本次菜单的作用对象（4.2c D13）：右键命中的是哪一只，菜单里的
    设置入口与「退出这只」就作用在哪一只。不给 = 主 sprite（兼容既有调用面）。
    """

    def __init__(self, shell, sprite=None) -> None:
        self._shell = shell
        self._sprite = sprite

    @property
    def sprite(self):
        """菜单作用对象（默认主 sprite）。"""
        return self._shell.sprite if self._sprite is None else self._sprite

    # ---------------------------------------------------------------- 宿主查找
    @property
    def _instance(self):
        """本壳的 PetInstance（app.py）：聊天/角色/视觉识别的进程内持有点。"""
        return getattr(self._shell, "_instance", None)

    @property
    def _app_shell(self):
        """AppShell（app.py）：余额/更新/待办/报时/节日/设置进程等进程级服务。"""
        return getattr(self._instance, "shell", None)

    def _hosts(self) -> tuple:
        return (self._app_shell, self._instance, self._shell)

    @property
    def _is_main_sprite(self) -> bool:
        """菜单作用对象是否主宠（决定逐只设置写不写主配置）。"""
        return self.sprite is not None and self.sprite is self._shell.sprite

    def _persist_sprite_setting(self, key: str, value) -> bool:
        """逐只设置的持久化口径（旧"写本窗自己那份 config"）。

        主宠 → 主配置（``Config.set`` + ``save``）；子宠 → 它自己的
        ``config-slot-N.json``（B7b：``OverlayShell.persist_sprite_setting`` 原子写
        单键，**绝不碰主配置**——此前子宠只改运行态、重启即丢，或误写主配置把主宠
        的尺寸/速率/物理改脏，见审计 C1/C2）。
        """
        persist = getattr(self._shell, "persist_sprite_setting", None)
        ok = bool(persist(self.sprite, key, value)) if callable(persist) else False
        if not ok:
            log.warning("overlay: 菜单设置保存失败 (%s)，运行态未应用", key)
            self._bubble("设置保存失败，未应用这次改动，请稍后重试", 6000)
        return ok

    # ---------------------------------------------------------------- 基础属性
    @property
    def cfg(self):
        """本次菜单作用对象那份配置（主宠 = 主配置；子宠 = 它自己的 slot 视图）。

        旧架构一窗一宠，菜单读的 ``pet.cfg`` 就是本窗那份配置——"切换角色"的勾选态
        也因此读的是被点那只的角色（B7b）。窗口级/进程级键在视图里原样转发主配置，
        全局条目（模板/联动/识屏）的读写口径不变。
        """
        provider = getattr(self._shell, "_sprite_config", None)
        if callable(provider):
            try:
                return provider(self.sprite)
            except Exception:
                log.debug("overlay: 取 sprite 配置视图失败", exc_info=True)
        return self._shell._config

    @property
    def scale(self) -> float:
        return float(self.sprite.scale)

    @property
    def mouse_through(self) -> bool:
        return bool(self._shell._user_mouse_through)

    @property
    def no_move(self) -> bool:
        return bool(getattr(self._behavior(), "no_move", False))

    @property
    def drag_physics(self) -> bool:
        return bool(getattr(self.sprite, "drag_physics", True))

    @property
    def playback_speed(self) -> float:
        return float(getattr(self.sprite, "playback_speed", 1.0))

    def _save(self) -> None:
        save = getattr(self.cfg, "save", None)
        if callable(save):
            save()

    def _bubble(self, text: str, duration_ms: int = 3200) -> bool:
        """按壳的气泡面冒一条；返回是否真上了屏（拿不到壳的气泡面 = 没上屏）。"""
        show = getattr(self._shell, "show_bubble", None)
        if not callable(show):
            return False
        try:
            return bool(show(text, duration_ms=duration_ms))
        except Exception:
            log.debug("overlay: facade 气泡展示失败", exc_info=True)
            return False

    # 共享建造器（harness_launcher / 音乐启动桥）会把本对象当「pet 宿主」用
    def show_bubble(self, text: str, duration_ms: int = 3200, **_ignored) -> bool:
        """宿主形气泡面（``launch_harness_gui`` / ``_MusicLaunchBridge`` 读它）。

        返回是否真上了屏：免打扰结束的汇报按它确认交付（见
        ``window_alerts.report_quiet_summary``），丢弃却报成功会把暂存排空。
        """
        return self._bubble(str(text), duration_ms)

    @property
    def dialog_parent(self):
        """模态对话框父窗口（QWidget）：共享建造器里 QMessageBox 需要真父窗。"""
        return getattr(self._shell, "overlay", None)

    # ---------------------------------------------------------------- 菜单图标面
    def icon_pixmap(self, size: int = 64) -> QPixmap:
        """「生小肥鱼」右侧头像：sprite 当前帧（裁白边/缩放由 icons 侧完成）。

        窗口版 ``PetWindow.icon_pixmap`` 取窗口当前帧；sprite 世界的等价物就是
        该 sprite 已渲染的帧位图（``_pixmap``），没有帧时回退到当前 clip 的
        ``currentImage()``。取不到就返回空 pixmap（菜单项自动无图标，不抛）。
        """
        sprite = self.sprite
        pm = getattr(sprite, "_pixmap", None)
        if pm is not None and not pm.isNull():
            return QPixmap(pm)
        clip = getattr(sprite, "_clip", None)
        current = getattr(clip, "currentImage", None)
        image = current() if callable(current) else None
        if image is None or image.isNull():
            return QPixmap()
        return QPixmap.fromImage(image)

    def _icon_cache(self) -> dict:
        """动画缩略图缓存挂**壳**上（facade 每次右键重建，缓存必须长寿命）。

        键 =（库身份, 动画名）（``shared.animation_icon_cache_key``）：同壳多宠
        异角色的同名动画各存各的，换库后旧 worker 的迟到写入也落在旧库的键上。
        值 =（库弱引用, QImage）（``shared.animation_icon_cache_entry``）：键里的
        ``id()`` 会被回收对象复用，命中时还要按值里的弱引用核对身份
        （``shared.animation_icon_cache_hit``）。
        """
        cache = getattr(self._shell, "_menu_icon_cache", None)
        if cache is None:
            cache = {}
            try:
                self._shell._menu_icon_cache = cache
            except Exception:
                log.debug("overlay: 挂载菜单图标缓存失败", exc_info=True)
        return cache

    def _icon_key(self, name: str, library=None) -> tuple[int, str]:
        """本 sprite 当前素材库下的缓存键（查询/写入必须同一函数，单一来源在 shared）。"""
        if library is None:
            library = getattr(self.sprite, "library", None)
        return animation_icon_cache_key(library, name)

    def animation_icon_cached_image(self, name: str):
        """只读缓存查询（shared 侧用它决定是否先放占位图标）。

        命中判定走 ``shared.animation_icon_cache_hit``：库身份在键里只是 ``id()``，
        真正的身份校验在值里的库弱引用上——旧库回收后 id 被新库复用时，这条查询
        必须报未命中，否则菜单会把上一只宠的缩略图显示在新角色上。
        """
        library = getattr(self.sprite, "library", None)
        cached = animation_icon_cache_hit(
            self._icon_cache().get(self._icon_key(name, library)), library)
        return QImage(cached) if cached is not None else QImage()

    def animation_icon_image(self, name: str):
        """解码一个代表性帧（worker 线程安全：只碰 clip_path + 解码器）。

        窗口版在 PetWindow 上缓存（128 条上限）；sprite 世界的缓存落在壳上，上限与
        失效口径逐点一致。

        库身份在**解码前**取一次并贯穿查询/解码/写入：worker 可能在解码期间被换库
        （换角色），此时它手里的图仍是旧库的，写入必须落回旧库的键——否则迟到写入会
        成为新库的缓存命中，菜单显示上一个角色的图。写入连库的**弱引用**一起存
        （``shared.animation_icon_cache_entry``）：键只是 ``id()``，命中还要核对值里的
        弱引用仍指向本次查询的这个库，所以 id 复用同样不会误命中。
        """
        library = getattr(self.sprite, "library", None)
        key = self._icon_key(name, library)
        cache = self._icon_cache()
        cached = animation_icon_cache_hit(cache.get(key), library)
        if cached is not None:
            return QImage(cached)
        clip_path = getattr(library, "clip_path", None)
        path = clip_path(name) if callable(clip_path) else None
        if path is None:
            return QImage()
        from .animation_thumbnail import decode_representative_frame

        image = decode_representative_frame(path)
        if image is not None and not image.isNull():
            if len(cache) >= 128:
                cache.clear()
            cache[key] = animation_icon_cache_entry(library, image)
        return image

    # ---------------------------------------------------------------- 素材清单（动画分类建造器）
    def _cats(self) -> dict:
        return self._behavior()._categories(self.sprite.library)

    @property
    def idles(self) -> list:
        return self._cats()["idles"]

    @property
    def turns(self) -> list:
        return self._cats()["turns"]

    @property
    def moves(self) -> list:
        return self._cats()["moves"]

    @property
    def clicks(self) -> list:
        return self._cats()["clicks"]

    @property
    def acts(self) -> list:
        return self._cats()["acts"]

    # ---------------------------------------------------------------- AI 对话/设置（聊天可用性门）
    def _chat_enabled(self) -> bool:
        """聊天可用性：与 ``OverlayShell._chat_enabled`` 同源（无聊天打包变体
        ``enable_chat=False`` / 实例缺 ``enable_chat`` 视作可用）。"""
        probe = getattr(self._shell, "_chat_enabled", None)
        if callable(probe):
            return bool(probe())
        return bool(getattr(self._instance, "enable_chat", True))

    @property
    def chat_build_capable(self) -> bool:
        """构建变体能力（不含 chat 模块闸）：harness 等按它判出场。"""
        return bool(getattr(self._instance, "chat_build_capable", True))

    @property
    def on_open_chat(self):
        """「AI 对话」：legacy 由 app 注入 ``PetInstance.open_chat``（纯桌宠版
        注入 None → 条目整条不显示）；这里同一门（``enable_chat``）。"""
        if not self._chat_enabled():
            return None
        return _first_callable(self._hosts(), ("open_chat", "open_full_chat"))

    @property
    def on_open_chat_settings(self):
        """「AI 设置」：``PetInstance.open_chat_settings``（同一聊天可用性门）。"""
        if not self._chat_enabled():
            return None
        return _first_callable(self._hosts(), ("open_chat_settings",))

    @property
    def on_open_modern_settings(self):
        """「桌宠设置」：``OverlayShell.open_settings_for``（D13 逐 sprite 身份）。

        必须闭包绑定本次菜单的作用对象：``open_settings_for`` 的默认参数是"主
        sprite"，直接把它交出去会让右击子肥鱼的设置入口静默打开主宠配置。
        """
        opener = getattr(self._shell, "open_settings_for", None)
        if not callable(opener):
            return None
        sprite = self.sprite
        return lambda: opener(sprite)

    @property
    def on_look_screen(self):
        """「看看屏幕」：``OverlayShell.look_at_screen(sprite)``（PetWindow.look_at_screen
        的 sprite 等价物，同样只在聊天可用时注入）。

        闭包绑定本次菜单的作用对象：答复气泡归被点的那一只（旧版一窗一宠天然
        如此），不绑就等于右击子肥鱼却让主肥鱼答话。
        """
        if not self._chat_enabled():
            return None
        fn = getattr(self._shell, "look_at_screen", None)
        if not callable(fn):
            return None
        sprite = self.sprite
        return lambda: fn(sprite)

    # ---------------------------------------------------------------- 进程级服务（AppShell）
    def _service_host(self):
        """手动服务结果（余额 / 天气）的宿主：**发起 sprite 的代次呈现器**。

        结果归被点的那一只（旧版一窗一宠天然如此），且请求在飞时把这一只关掉之后，
        迟到结果只取消呈现——不再冒到别宠头上（2026-10-10 终审 A10；点击余额路径
        ``overlay_shell:2449`` 早就用 ``_sprite_host(target)`` 收口了，菜单旁路漏了）。
        拿不到壳的呈现器面（legacy 窗口宿主 / 测试替身）时回退壳本身：那是历史口径，
        至少保证"结果有地方落"。
        """
        maker = getattr(self._shell, "sprite_presenter", None)
        if callable(maker):
            try:
                host = maker(self.sprite)
            except Exception:
                log.debug("overlay: 取 sprite 服务呈现器失败", exc_info=True)
            else:
                if host is not None:
                    return host
        return self._shell

    @property
    def on_show_balance(self):
        """「DeepSeek 余额」：``AppShell.show_balance``（app._wire_window 注入的
        同一个方法）；父窗传**发起 sprite 的呈现器**（呈现代次收口，见
        :meth:`_service_host`）。壳本身也是合法宿主（``cfg`` / ``show_bubble`` /
        ``request_link_anim`` 全套面），故壳不可用时不会出现 AttributeError。
        """
        if not self._chat_enabled():
            return None
        fn = _first_callable(self._hosts(), ("show_balance",))
        if fn is None:
            return None
        host = self._service_host()
        return lambda *_ignored: fn(host)

    @property
    def on_show_weather(self):
        """「查看天气」：``AppShell.show_weather``；父窗同样是发起 sprite 的呈现器。

        与 ``on_show_balance`` 的差别只有一条：**不**受聊天能力门控——天气取的是
        独立外部数据源，没有聊天/DSL 变体也不该消失。
        """
        fn = _first_callable(self._hosts(), ("show_weather",))
        if fn is None:
            return None
        host = self._service_host()
        return lambda *_ignored: fn(host)

    @property
    def on_check_update(self):
        """「检查更新」：``AppShell.check_update``（窗口版是 ``_slot_wrap`` 包装，
        这里壳本身即唯一窗，无需槽位前缀）。

        父窗同 ``on_show_balance`` 传壳：传 OverlayWindow 会在
        ``target.show_bubble`` 抛 AttributeError——而那句在
        ``self._update_checking = True`` 之后、复位 connect 之前，异常会让
        本次会话永久不再检查更新。
        """
        fn = _first_callable(self._hosts(), ("check_update",))
        if fn is None:
            return None
        shell = self._shell
        return lambda *_ignored: fn(shell)

    @property
    def on_open_todo_panel(self):
        return _first_callable(self._hosts(), ("open_todo_panel",))

    @property
    def on_network_info(self):
        """「网络信息」：``AppShell.open_network_info``（进程级弹窗，与窗口版同一方法）。

        弹窗按当前网络环境取一次数据，与"哪一只宠被右击"无关，故不绑 sprite——
        但仍必须在 facade 上存在：注册表的可用性判定要求 ``on_network_info``
        可调用（``registry._network_info_available``），缺了这个属性 overlay 菜单
        会静默少一项（tests/test_sprite_menu_parity.py 逐项比对拦得住）。
        """
        return _first_callable(self._hosts(), ("open_network_info",))

    @property
    def on_voice_chime_now(self):
        return _first_callable(self._hosts(), ("trigger_voice_chime_now",))

    @property
    def on_toggle_voice_chime(self):
        return _first_callable(self._hosts(), ("toggle_voice_chime",))

    @property
    def on_festival_now(self):
        return _first_callable(self._hosts(), ("trigger_festival_now",))

    @property
    def on_toggle_festival(self):
        return _first_callable(self._hosts(), ("toggle_festival_reminder",))

    @property
    def on_divination_draw(self):
        """「抽支签」：``AppShell.trigger_divination_draw``。

        **必须在 facade 上存在**：注册表的可用性判定是
        ``callable(getattr(pet, "on_divination_draw", None))``，门面不代理这个属性，
        overlay 菜单就会**静默少一项**（语音报时/节日四项同样依赖各自的代理）。
        """
        return _first_callable(self._hosts(), ("trigger_divination_draw",))

    def _behavior(self, sprite=None):
        """该 sprite **所在宿主**的行为控制器。

        多窗（混合缩放）下每个宿主各持一份：写主窗那份，被点的副屏宠根本不知道
        ——实机表现是"副屏选动画/移动没反应、过一拍被覆盖"。
        """
        shell = self._shell
        target = self.sprite if sprite is None else sprite
        host = shell.host_for_sprite(target) if target is not None else None
        return host.behavior if host is not None else shell.behavior

    # ---------------------------------------------------------------- 动画/速率
    def switch_clip(self, name: str) -> None:
        """播放指定动画（一次性，播完回掷骰链——window.py switch_clip 语义）。

        作用对象 = 被点的那一只（旧版一窗一宠 ``self`` 的等价物）。
        """
        self._behavior().play_once(self.sprite, name)

    def trigger_move(self, name: str) -> bool:
        """以指定移动素材触发一次移动（window.py trigger_move 语义）。"""
        return self._behavior().play_move_once(self.sprite, name)

    def set_playback_speed(self, value: float) -> bool:
        value = float(value)
        if not self._persist_sprite_setting("playback_speed", value):
            return False
        sprite = self.sprite
        if sprite.playback_speed != value:
            sprite.playback_speed = value
            setter = getattr(getattr(sprite, "_clip", None), "set_playback_speed", None)
            if callable(setter):
                setter(value)
        return True

    # ---------------------------------------------------------------- 窗口能力
    def change_scale(self, scale: float) -> bool:
        scale = float(scale)
        if not self._persist_sprite_setting("scale", scale):
            return False
        if self.sprite.scale != scale:
            self.sprite.scale = scale
            hook = getattr(self._shell, "on_sprite_scale_changed", None)
            if callable(hook):
                hook(self.sprite)
        return True

    def set_drag_physics(self, on: bool) -> bool:
        if not self._persist_sprite_setting("drag_physics", bool(on)):
            return False
        self.sprite.drag_physics = bool(on)
        return True

    def set_no_move(self, on: bool) -> None:
        # 全局开关：所有宿主都要同步，否则副屏那只照旧乱走
        for host in getattr(self._shell, "hosts", lambda: [])():
            host.behavior.no_move = bool(on)
        self.cfg.set("no_move", bool(on))
        self._save()

    def set_mouse_through(self, on: bool) -> None:
        self._shell.overlay.set_mouse_through(bool(on))

    def set_on_top(self, on: bool) -> None:
        self._shell.set_on_top(bool(on))

    def go_default_corner(self) -> None:
        # 收敛到壳的同名方法：那里在落位前取消探头会话（会话残留会让角色斜着
        # 停在角落、下一次点击被拉直过渡瞬移回旧屏缘）。
        self._shell._go_default_corner(self.sprite)

    def hide(self) -> None:
        """「隐藏桌宠」：只隐藏被点的那一只（旧版一窗一宠 = 隐藏本窗）。

        子宠走逐只显隐（``set_sprite_visible``，托盘逐只菜单「显示这只」可恢复，
        且其它宠不受影响）。主宠保持整窗语义（``set_pet_visible``）：主宠即宿主
        合成窗，藏它一只等于整窗没有内容可画——那条路顶层「显示 / 隐藏」已经按
        整窗语义给过（``_refresh_tray_menu`` 的逐只「显示这只」也只在 ≥2 只时挂）。
        """
        sprite = self.sprite
        if sprite is None or sprite is self._shell.sprite:
            self._shell.set_pet_visible(False)
            return
        self._shell.set_sprite_visible(sprite, False)

    # ---------------------------------------------------------------- 黄金回旋/边缘探头
    def trigger_golden_spin(self) -> None:
        """「黄金回旋」：``OverlayShell.trigger_golden_spin(sprite)``（sprite 世界
        没有 GoldenSpinController，壳用同一组角度常量做 sprite 等价实现）。

        目标 = 被点的那一只（旧版一窗一宠天然只有被点那只）。
        """
        fn = getattr(self._shell, "trigger_golden_spin", None)
        if callable(fn):
            fn(self.sprite)

    def set_edge_probe_enabled(self, on: bool) -> None:
        """「边缘探头」开关：只写 config 键。

        等价性：sprite 世界的探头（``sprite_edge_probe``）每 tick 进入判定前
        热读 ``edge_probe_enabled``（见 overlay_shell._build 的注释），而窗口版
        ``set_edge_probe_enabled`` = 写配置 + ``controller.set_enabled``——后者
        读的就是同一个键，故直写配置即等价、立即生效。
        """
        self.cfg.set("edge_probe_enabled", bool(on))
        self._save()

    # ---------------------------------------------------------------- 角色/设置/退出
    def request_switch_character(self, character_id: str) -> None:
        """「切换角色」：作用 = **被点的那一只**（旧版一窗一宠只切本窗）。

        子宠换它自己的库并写它自己的 ``config-slot-N.json['character']``；此前
        无论点哪只都打在主宠身上（审计 F1）。
        """
        self._shell.switch_character(str(character_id), self.sprite)

    def rename_character(self) -> None:
        """「重命名当前角色…」：window.py ``rename_character`` 的 sprite 等价物。

        只差父窗口（overlay 窗代替 PetWindow 作模态父），配置读写与气泡逐点一致。
        """
        cid = str(self.cfg.get("character", catalog.DEFAULT_CHARACTER))
        current = self.cfg.character_display_name(cid)
        name, ok = QInputDialog.getText(
            getattr(self._shell, "overlay", None), "重命名角色",
            f"给 {cid} 起个名字（留空恢复默认）：", text=current,
        )
        if not ok:
            return
        self.cfg.set_character_alias(cid, name)
        self._bubble(f"角色名：{self.cfg.character_display_name(cid)}")

    def on_spawn_pet(self) -> None:
        self._shell.spawn_pet()

    def on_clear_spawned_pets(self) -> None:
        self._shell.clear_spawned_pets()

    def on_open_settings(self) -> None:
        """打开设置页（D13：按被点 sprite 的 config 身份传给独立设置进程）。"""
        opener = getattr(self._shell, "open_settings_for", None)
        if callable(opener):
            opener(self.sprite)

    def on_exit_pet(self) -> None:
        """关闭被点宠物，档案保留；主子同样不确认，退出应用另有入口。"""
        sprite = self.sprite
        if sprite is not None:
            self._shell.exit_pet(sprite)

    def close(self) -> None:
        self._shell.app.quit()

    def request_quit(self) -> None:  # add_quit 的调用面
        self.close()

    # ---------------------------------------------------------------- 截图（快速识屏）
    #
    # 入口一律**转发到壳**（``OverlayShell.on_screen_capture``）：截图编排件
    # 要活得比菜单久（一次采集会话横跨菜单关闭），而本 facade 每次右键都重建，
    # 不能当宿主——宿主是壳（见 ``overlay_shell._screen_capture``）。
    @property
    def on_screen_capture(self):
        """「截图 → 框选识屏」：转发到壳的入口。"""
        return getattr(self._shell, "on_screen_capture", None)

    # ---------------------------------------------------------------- 主动识屏
    def _proactive_watcher(self):
        """共享识屏监视器：壳注入面优先，缺失时从 AppShell 的 SharedSubsystems
        回填（等价 window.py ``_ensure_proactive_watcher`` 的懒创建）。"""
        watcher = getattr(self._shell, "proactive_watcher", None)
        if watcher is None:
            shared = getattr(self._app_shell, "_shared", None)
            watcher = getattr(shared, "proactive", None)
            if watcher is not None:
                try:
                    self._shell.proactive_watcher = watcher
                except Exception:
                    log.debug("overlay: 回填 proactive 监视器失败", exc_info=True)
        return watcher

    def _apply_proactive(self) -> None:
        apply_config = getattr(self._proactive_watcher(), "apply_config", None)
        if callable(apply_config):
            try:
                apply_config()
            except Exception:
                log.debug("overlay: proactive apply_config 失败", exc_info=True)

    def toggle_proactive_enabled(self, on: bool) -> None:
        """主动识屏总开关（window.py ``_toggle_proactive_enabled`` 等价）。"""
        pro_data = dict(self.cfg.get("proactive_screen", {}) or {})
        pro_data["enabled"] = bool(on)
        self.cfg.set("proactive_screen", pro_data)
        self._save()
        self._apply_proactive()
        if on:
            from .proactive import effective_proactive_config

            eff = effective_proactive_config(self.cfg.get("proactive_screen", {}))
            if eff["whitelist"]:
                self._bubble("主动识屏已开启～我会偶尔看看你正在用的软件", 4000)
            else:
                self._bubble(
                    "主动识屏已开启～但白名单还是空的，在 右键→主动识屏→打开设置 "
                    "里添加要观察的应用后我才会开始工作",
                    6000,
                )

    def set_proactive_option(self, key: str, value) -> None:
        """主动识屏子项（window.py ``_set_proactive_option`` 等价）。"""
        pro_data = dict(self.cfg.get("proactive_screen", {}) or {})
        pro_data[key] = value
        self.cfg.set("proactive_screen", pro_data)
        self._save()
        self._apply_proactive()

    # ---------------------------------------------------------------- Agent 联动
    def _agent_manager(self):
        """共享联动管理器：壳注入面优先，缺失时从 SharedSubsystems 回填
        （等价 window.py ``_ensure_agent_link_manager``）。"""
        manager = getattr(self._shell, "agent_link_manager", None)
        if manager is None:
            shared = getattr(self._app_shell, "_shared", None)
            manager = getattr(shared, "agent_link", None)
            if manager is not None:
                try:
                    self._shell.agent_link_manager = manager
                except Exception:
                    log.debug("overlay: 回填联动管理器失败", exc_info=True)
        return manager

    def toggle_agent_link(self, agent_key: str, on: bool, action=None) -> None:
        """Agent 联动子项（window.py ``_toggle_agent_link`` 等价，含拒绝回滚）。

        ``set_enabled`` 返回 False（用户拒绝授权 / hooks 安装失败）时必须把菜单
        勾选态回滚，否则 UI 显示已开启而实际未生效。
        """
        manager = self._agent_manager()
        if manager is not None:
            ok = manager.set_enabled(agent_key, on)
            if not ok:
                if action is not None:
                    action.blockSignals(True)
                    action.setChecked(not on)
                    action.blockSignals(False)
                return
        else:
            ag_data = dict(self.cfg.get("agent_link", {}) or {})
            ag_data[agent_key] = bool(on)
            self.cfg.set("agent_link", ag_data)
            self._save()
        if on:
            self._bubble(f"已开启 {agent_key.upper()} 状态联动监听～", 4000)


    # ---------------------------------------------------------------- 音乐（歌词）宿主
    def _music_host(self):
        """音乐服务宿主：AppShell → PetInstance → 本壳（首个持有宿主面的）。"""
        for host in self._hosts():
            if host is None:
                continue
            if callable(getattr(host, "install_music_lyric", None)):
                return host
            if getattr(host, "_music_lyric", None) is not None:
                return host
        return None

    @property
    def music_service_available(self) -> bool:
        """音乐服务是否可达（不可达 → 整组「音乐」子菜单不注入）。"""
        return self._music_host() is not None

    @property
    def _music_lyric(self):
        host = self._music_host()
        return getattr(host, "_music_lyric", None) if host is not None else None

    def install_music_lyric(self):
        """``shared.set_music_mode`` 的安装面（幂等；无宿主 → None 静默降级）。"""
        host = self._music_host()
        installer = getattr(host, "install_music_lyric", None)
        return installer() if callable(installer) else None

    # ---------------------------------------------------------------- 菜单原位重开
    def reopen_context_menu(self, menu) -> None:
        """按原位立即重开右键菜单（window.py ``reopen_context_menu`` 的 overlay
        等价物：壳层关旧菜单 + 延迟重开）。"""
        host = self._shell.host_for_sprite(self.sprite) if self.sprite is not None else None
        window = host.overlay if host is not None else getattr(self._shell, "overlay", None)
        reopen = getattr(window, "reopen_context_menu", None)
        if callable(reopen) and menu is not None:
            reopen(menu)


def _is_music_group(submenu: QMenu) -> bool:
    """「音乐」子菜单判定：标题或内容任一命中。

    用户编排（``context_menu_layout``）允许给子菜单改别名，只按标题匹配会漏；
    两个模板的播放器条目文案都是「打开…给主人放歌」（``shared._music_player_builder``），
    故内容兜底是稳定的。
    """
    if submenu.title() == "音乐":
        return True
    for action in submenu.actions():
        text = action.text()
        if text.startswith("打开") and text.endswith("给主人放歌"):
            return True
    return False


def _drop_music_group(menu: QMenu) -> None:
    """摘掉「音乐」子菜单（服务不可达时的整组隐藏）。"""
    for action in list(menu.actions()):
        submenu = action.menu()
        if submenu is None or not _is_music_group(submenu):
            continue
        menu.removeAction(action)
        submenu.deleteLater()
        return


def _move_before_quit(menu: QMenu, action) -> None:
    """把新增项插到「退出」之前（保持"设置/退出这只 紧邻退出"的既有分组）。"""
    for candidate in menu.actions():
        if candidate is action:
            continue
        if candidate.text() == "退出":
            menu.removeAction(action)          # 显式摘除再插入 = 确定性移动
            menu.insertAction(candidate, action)
            return


def build_sprite_full_menu(shell, sprite=None) -> QMenu:
    """overlay 全量右键菜单：现代建造器（注册表 + 用户编排）原样复用。

    ``sprite`` = 被点中的那一条（4.2c D13 由 ``ShellOverlayWindow.contextMenuEvent``
    透传）；动作面里作用于"某一只"的入口（桌宠设置 / 退出这只）按它路由。

    条件显示与窗口侧逐点一致（聊天门 / win32+聊天门 / 音乐服务门），
    ``tests/test_sprite_menu_parity.py`` 用真实 PetWindow 的同条件菜单树锁死。
    「隐藏桌宠」不再由本函数补位：它随注册表的 ``hide_pet`` 落在「桌宠控制」
    子菜单里，与窗口侧同一棵树（补位会变成同名重项）。
    """
    facade = SpriteMenuFacade(shell, sprite)
    cfg = facade.cfg
    menu = QMenu()
    apply_modern_menu_style(
        menu, cfg.get("context_menu_appearance", {}) or {})
    build_modern_menu(menu, facade)
    install_modern_check_indicators(menu)
    install_stay_open_interaction(menu)
    if not facade.music_service_available:
        # 音乐服务宿主不可达 → 整组隐藏（注册表不按宿主能力过滤，在这里收口）
        _drop_music_group(menu)
    # 「退出这只」只在多宠时出现（窗口侧单窗不注入该入口，只有 app.quit 语义的
    # 「退出」）——没有子肥鱼时它等价于「退出」，不重复列出
    if getattr(shell, "_spawned", None):
        exit_action = _add_action(menu, "退出这只", None, facade.on_exit_pet,
                                  close_on_trigger=True)
        _move_before_quit(menu, exit_action)
    # F5 教训：facade 与菜单同寿命（wrapper 回收后回调命中失效引用）
    menu._facade = facade
    return menu
