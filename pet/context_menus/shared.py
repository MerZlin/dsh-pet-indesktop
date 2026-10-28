# -*- coding: utf-8 -*-
"""Stable leaf-menu primitives shared by the two independent layouts."""
from __future__ import annotations

import logging
import os
import sys
import threading
import time
import weakref

import shiboken6

from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QActionGroup, QDesktopServices, QIcon, QPixmap
from PySide6.QtCore import QProcess
from PySide6.QtWidgets import QMenu

from .. import autostart as autostart_mod
from .. import catalog
from .. import feature_activation
from ..harness_launcher import launch_harness_gui
from ..updater import QUARK_PAN_URL as QUARK_PAN_URL, REPO_URL as REPO_URL
from .icons import fitted_pet_pixmap_icon, pet_avatar_menu_icon, vector_menu_icon
from .menu_styles.common import inherit_menu_style

log = logging.getLogger(__name__)

DEEPSEEK_WEB_URL = "https://chat.deepseek.com/"


def animation_icon_cache_key(library, name: str) -> tuple[int, str]:
    """菜单动画缩略图缓存键 =（素材库身份, 动画名），查询与写入共用这一处。

    为什么必须带库身份：缓存挂壳上、facade 每次右键重建，只按动画名做键时——

    - 同壳两只宠本来就不同角色（没有任何切换发生）：同名动画直接命中对方的缩略图，
      菜单里显示的是另一只宠的图；
    - 换库后旧 worker 才解码完：它把**旧库**的图写回裸名称键，新库随后命中它。

    库身份用 ``id(library)``（进程内唯一）：库对象一旦回收，旧键自然成为死项，由缓存
    的 128 条上限兜底清掉。但 ``id()`` 只保证**同刻存活**的对象互不相同——对象回收后
    新对象可以复用同一个 id，所以"键相同"不等于"是同一个库"：命中判定必须再看缓存值里
    带的库弱引用（``animation_icon_cache_entry`` / ``animation_icon_cache_hit``）。
    本函数只算键，不负责身份校验。
    """
    return (id(library), str(name))


def animation_icon_cache_entry(library, image) -> tuple:
    """把缩略图包成缓存**值**：``(库弱引用, QImage)``——键只带 id，值带身份。

    为什么值不能只是 QImage：``id(library)`` 会被回收对象复用 ⇒ 光看键会把**另一个**
    库的图当成本库的命中（新角色菜单显示上一只宠的缩略图）。值里带一份库的弱引用做
    身份校验（见 ``animation_icon_cache_hit``）：弱引用不持有库、不给库延寿（把库强引用
    塞进缓存会把退役的库吊在内存里），旧库回收后校验必然失配、该条目退化成"永不命中"，
    下次查询重解码并按同一个键覆盖。

    不支持弱引用的库对象（``weakref.ref`` 抛 ``TypeError``）拿不到身份凭证 ⇒ 命中判定
    一律判未命中（宁可重解码，不冒把错图画上屏的风险）。
    """
    try:
        return (weakref.ref(library), image)
    except TypeError:
        return (None, image)


def animation_icon_cache_hit(entry, library):
    """缓存值命中判定：命中返回那份 QImage，未命中返回 ``None``。

    命中 = 值形如 ``animation_icon_cache_entry`` ∧ 库弱引用仍存活 ∧ 指向的**正是**本次
    查询的这个库对象。三条缺一即未命中（调用方重解码并覆盖）：id 复用、库已回收、
    条目被别处写坏，都不会把旧库的图画上屏。
    """
    if not isinstance(entry, tuple) or len(entry) != 2:
        return None
    ref, image = entry
    if ref is None or ref() is not library:
        return None
    return image


def animation_icon_cache_drop_library(cache: dict, library) -> int:
    """按库身份定向摘掉缓存条目（退出子肥鱼）；返回摘掉的条数。

    键只带 ``id()``，没有"库 → 键"的反查表，所以逐个条目比身份。除该库自己的条目外，
    引用已死（库已回收）与没有身份凭证（不支持弱引用的库）的条目一并摘掉——这两类对
    任何库都是永不命中，留着只占 128 条上限与内存。
    """
    if not isinstance(cache, dict) or library is None:
        return 0
    removed = 0
    for key, entry in list(cache.items()):
        ref = entry[0] if isinstance(entry, tuple) and len(entry) == 2 else None
        owner = ref() if ref is not None else None
        if ref is None or owner is None or owner is library:
            cache.pop(key, None)
            removed += 1
    return removed


class _AnimationIconFailure:
    """解码失败的收口载荷（动画名 + 异常本体），与成功的 QImage 共用同一个 ready 信号。

    壳里不装异常以外的状态：worker 线程只产出这份载荷，一切对宿主的访问
    （写 QAction、写日志、续泵）都留在 GUI 线程的 applier 里。
    """

    __slots__ = ("animation_name", "error")

    def __init__(self, animation_name: str, error: BaseException) -> None:
        self.animation_name = animation_name
        self.error = error


class _AnimationIconSignals(QObject):
    ready = Signal(object)


class _AnimationIconWorker(QRunnable):
    """解码一个动画缩略图；**成功与失败都必须在任务边界收口成一次 ready 发射**。

    为什么失败不能留给 Qt 去打印：``run`` 里逃出去的异常由 shiboken 打印
    ``Error calling Python override of QRunnable::run():`` 后吞掉，本对象既不发射信号，
    也没人摘掉 ``submenu._animation_icon_workers`` 里的在岗记录；而泵（见
    ``_populate_animation_category.pump``）按"在岗 worker < 2"放行 —— 两只失败就把队列
    **永久卡死**，同一菜单里排在后面的图标再也不会解码，且失败原因只剩一行被 pytest
    捕获走的 shiboken 输出。所以这里把一般异常（``Exception``；``KeyboardInterrupt`` /
    ``SystemExit`` 属 ``BaseException``，照旧穿透）转成失败载荷照常发射，收口路径与成功
    完全一致，只差"不上图标"。

    归属：C++ 侧由 ``QThreadPool.start`` 接管（默认 autoDelete，run 返回后删掉）；
    Python 壳由 ``submenu._animation_icon_workers`` 记账（泵按它数在岗）。宿主先销毁时
    没有消费者，结果丢弃即可，不放宽菜单树的释放路径。
    """

    def __init__(self, loader, animation_name: str):
        super().__init__()
        self.loader = loader
        self.animation_name = animation_name
        self.signals = _AnimationIconSignals()

    def run(self) -> None:
        payload = None
        try:
            payload = self.loader(self.animation_name)
        except Exception as exc:
            payload = _AnimationIconFailure(self.animation_name, exc)
        signals = self.signals
        if shiboken6.isValid(signals) is False:
            # 信号源已销毁（宿主先一步收口）：没有消费者，结果丢弃。不碰任何宿主对象，
            # 保证 worker 线程永不写已删的 Qt 对象。
            return
        try:
            signals.ready.emit(payload)
        except RuntimeError:
            # 发射这一瞬间信号源被销毁（与菜单树释放的窗口竞态）：同上，无消费者。
            pass


class _AnimationIconApplier(QObject):
    """GUI 线程槽：接收 worker 解码完成信号并更新 QAction。

    挂在 submenu 下随菜单生命周期存在；worker 线程只负责解码，
    ready 信号经 Qt 自动队列投递到本对象（GUI 线程），避免跨线程
    操作 QMenu/QAction（Qt 未定义行为，可致偶发崩溃/图标错乱）。

    成功与失败走**同一条**收口：手里是 QImage 还是失败载荷，都要摘掉本 worker 的在岗
    记录并续泵（漏掉任何一条，队列就卡在两只失败上）；失败只多一步有原因的日志，
    代价是"这一项保持占位图标"，其余图标照常继续。
    """

    def __init__(self, submenu, action, worker, pump, parent=None):
        super().__init__(parent)
        self._submenu = submenu
        self._action = action
        self._worker = worker
        self._pump = pump

    def _report_failure(self, submenu, failure: _AnimationIconFailure) -> None:
        """有原因、且不按图标刷屏：同一菜单里第一条记 warning，其余降到 debug。"""
        reason = f"{type(failure.error).__name__}: {failure.error}"
        if getattr(submenu, "_animation_icon_failure_logged", False):
            log.debug(
                "菜单动画缩略图解码失败（同类已记过）：动画=%s（%s）",
                failure.animation_name, reason,
            )
            return
        submenu._animation_icon_failure_logged = True
        log.warning(
            "菜单动画缩略图解码失败：动画=%s（%s）；本菜单内后续失败只记 debug",
            failure.animation_name, reason, exc_info=failure.error,
        )

    @Slot(object)
    def on_ready(self, result) -> None:
        submenu = self._submenu
        if shiboken6.isValid(submenu) is False:
            return
        workers = getattr(submenu, "_animation_icon_workers", None)
        if workers:
            # 按身份摘（不是 ==）：此刻 worker 的 C++ 侧多已被 QThreadPool 自动删除，
            # 只剩 Python 壳，身份比较不碰已删对象。
            for index, item in enumerate(workers):
                if item is self._worker:
                    del workers[index]
                    break
        if isinstance(result, _AnimationIconFailure):
            self._report_failure(submenu, result)
        # setIcon() invalidates QMenu's action geometry. Doing
        # that dozens of times on a visible, scrollable menu
        # corrupts its scroll layout and blocks hover events.
        elif (
            not submenu.isVisible()
            and result is not None
            and not result.isNull()
        ):
            self._action.setIcon(fitted_pet_pixmap_icon(submenu, QPixmap.fromImage(result)))
            submenu.update()
        pump = self._pump
        if callable(pump):
            pump()


def _root_menu(menu: QMenu) -> QMenu:
    root = menu
    while isinstance(root.parent(), QMenu):
        root = root.parent()
    return root


def take_deferred_menu_callbacks(menu: QMenu) -> list:
    """Take commands that must run after the native menu tracking loop."""
    callbacks = list(getattr(menu, "_deferred_callbacks", ()))
    menu._deferred_callbacks = []
    return callbacks


def defer_menu_callback(menu: QMenu, callback) -> bool:
    """Run a command after native menu tracking ends when the menu is open."""
    if not menu.isVisible():
        callback()
        return False
    root = _root_menu(menu)
    root._deferred_callbacks = list(
        getattr(root, "_deferred_callbacks", ())
    ) + [callback]
    root.close()
    return True


# 托盘菜单的"菜单关干净了吗"轮询预算：50ms × 60 ≈ 3s，与
# ``app.py::_present_dialog`` 的重试预算同口径（慢平台也不至于提前放弃）。
_MENU_CLOSE_POLL_MS = 50
_MENU_CLOSE_POLL_MAX = 60


def _menu_open(menu: QMenu) -> bool:
    """菜单是否还在显示态（C++ 侧已销毁按"已关闭"处理）。"""
    try:
        return bool(menu.isVisible())
    except RuntimeError:
        return False


def _menu_tree(menu: QMenu) -> list:
    """菜单所在的**整棵** popup 树：根菜单 + 各级子菜单。

    传入子菜单时必须回到根：子菜单关掉不等于菜单会话结束——根菜单还开着就仍处于
    同一个原生跟踪会话里（macOS 的 AppKit NSMenu 尤其如此；Windows 的 Qt popup 下
    根菜单关闭会顺带收掉子菜单，所以那条路径只是**碰巧**等价）。``QMenu.addMenu``
    建的子菜单以父菜单为 parent，``findChildren`` 递归拿到全部层级。
    """
    root = _root_menu(menu)
    return [root, *root.findChildren(QMenu)]


def run_after_menu_closed(menu: QMenu, callback) -> None:
    """关闭菜单，并把命令推迟到菜单（及其原生跟踪会话）结束后执行。

    **为什么托盘菜单不能复用 ``defer_menu_callback``**：

    1. 那条路依赖 ``menu.exec()`` 返回后的 ``take_deferred_menu_callbacks``
       派发点，而托盘菜单由 ``QSystemTrayIcon`` 原生弹出，进程里没有这个收口点；
    2. 它用 ``menu.isVisible()`` 判断"是否在菜单里"，而 macOS 的托盘菜单是
       AppKit 原生 NSMenu（Qt 侧 QMenu 从未 ``show()``，``isVisible()`` 恒为
       False）——判定会直接退回内联执行，正是要避开的：原生菜单跟踪会话中
       新建/激活窗口会被 AppKit 抑制，表现为「点了没反应」（issue #4 同族，
       右键菜单路径已在 window.py / overlay_shell 用 deferred 队列解决）。

    因此托盘路径的规则是"菜单先关、等它真的不在了再执行"：先按既有
    ``close_on_trigger`` 语义关闭菜单，再用定时器轮询确认它已关闭（预算 3s；
    病态情况下宁可晚执行也不吞掉用户的点击）。Cocoa 的菜单跟踪跑在自身
    run loop 模式，会压住这次定时器，命令自然落在跟踪会话结束之后。

    **关闭与观测的对象是整棵树，不是传进来的那一个菜单**（``_menu_tree``）：
    子菜单条目（``add_tray_action(sub, ...)``）以前只看 ``sub`` 自己，而子菜单
    关闭并不等于会话结束——根菜单还开着就还在同一次原生跟踪里，回调此时执行照样
    会被 AppKit 抑制。逐只子菜单的三条动作正是这么接的。

    定时器不绑 context（``AppShell`` 不是 QObject）；轮询链由闭包持有菜单树与
    回调，最长 3 秒，不会长期吊住对象。
    """
    tree = _menu_tree(menu)
    for item in tree:
        close = getattr(item, "close", None)
        if callable(close):
            try:
                close()
            except RuntimeError:
                pass

    def check(attempt: int = 0) -> None:
        if any(_menu_open(item) for item in tree):
            if attempt >= _MENU_CLOSE_POLL_MAX:
                callback()
                return
            QTimer.singleShot(_MENU_CLOSE_POLL_MS, lambda: check(attempt + 1))
            return
        callback()

    QTimer.singleShot(0, check)


def add_tray_action(menu: QMenu, text: str, callback):
    """托盘菜单条目：菜单先关、回调延到菜单关闭后执行（见 :func:`run_after_menu_closed`）。

    与 ``add_action`` 分开而不是加个开关：托盘菜单没有 QAction 图标列，也不走
    ``closeOnTrigger`` 属性（那条路要 ``menu.exec()`` 的收口点）。
    """
    action = menu.addAction(text)
    action.triggered.connect(
        lambda _checked=False: run_after_menu_closed(menu, callback))
    return action


def release_menu_tree(menu: QMenu) -> None:
    """释放整棵右键菜单树（``menu.exec()`` 返回后必须调用一次）。

    菜单以**长命窗口**为 parent，只丢 Python 引用时 C++ 侧的整棵树（子菜单/动作/
    动画图标解码池/图标 pixmap）会随每次右键永久累积——实测 offscreen 经 facade
    构建 modern 全量菜单 10 轮：+153 QMenu / +780 QAction；走本函数后增量 0。
    根因是 ``apply_modern_menu_style`` / ``install_modern_check_indicators`` 的
    ``aboutToShow.connect(lambda menu=menu: ...)`` 自引用连接让 Python 侧也永不
    释放（详见 ``tests/test_menu_tree_release.py``）。

    流程与 window.py 原实现逐位一致：

    1. 先 ``pool.clear()`` 掉各动画分类子菜单尚未启动的解码任务——否则
       QThreadPool 析构时会在 GUI 线程等运行中的 worker；
    2. 再有界轮询等 worker 收工（每 50ms 一次，总上限 3s，不阻塞事件循环；解码
       worker 病态不结束时也强制释放，否则菜单树永久滞留），然后
       ``menu.deleteLater()``。轮询定时器绑 menu 作 context：菜单先销毁时定时器
       随之失效，不会对已删 C++ 对象再 ``deleteLater``。
    """
    pools = []
    for submenu in menu.findChildren(QMenu):
        pool = getattr(submenu, "_animation_icon_pool", None)
        if pool is not None:
            pool.clear()
            pools.append(pool)

    def delete_when_idle(_attempts: int = 0) -> None:
        if _attempts >= 60:
            menu.deleteLater()
            return
        if any(not pool.waitForDone(0) for pool in pools):
            QTimer.singleShot(50, menu, lambda: delete_when_idle(_attempts + 1))
            return
        menu.deleteLater()

    if pools:
        delete_when_idle()
    else:
        menu.deleteLater()


def connect_action(action, callback) -> None:
    def invoke(_checked=False, action=action, callback=callback) -> None:
        parent = action.parent()
        if (
            bool(action.property("closeOnTrigger"))
            and isinstance(parent, QMenu)
            and parent.isVisible()
        ):
            defer_menu_callback(parent, callback)
            return
        callback()

    action.triggered.connect(invoke)


def add_action(
    menu: QMenu, text: str, icon_name: str | None, callback=None, *,
    close_on_trigger: bool = False,
):
    action = menu.addAction(vector_menu_icon(menu, icon_name) if icon_name else QIcon(), text)
    action.setProperty("closeOnTrigger", bool(close_on_trigger))
    if callback is not None:
        connect_action(action, callback)
    return action


def add_submenu(menu: QMenu, text: str, icon_name: str | None = None) -> QMenu:
    submenu = QMenu(text, menu)
    menu.addMenu(submenu)
    menu._owned_submenus = getattr(menu, "_owned_submenus", []) + [submenu]
    inherit_menu_style(menu, submenu)
    if icon_name:
        submenu.setIcon(vector_menu_icon(menu, icon_name))
    return submenu


# ------------------------------------------------------------ 随机动作前缀分组
# 2026-10-07 实机：随机动作 95 项单列 236×1439px，顶满全屏。清单里的前缀本就
# 系统化（吃* / 工作状态-* / 余额-* / 碎碎念-* / 原地* / 放*），按前缀再分一层
# 后每级回到一屏内；判定全部来自清单本身（不硬编码字符表），角色包日后新增
# `乐器-*` 这类前缀会自动成族。
ANIMATION_GROUP_RESIDUAL_LABEL = "更多"
# 家族最少成员数：少于 3 项的"共同前缀"多半只是巧合，不值得多一层菜单。
ANIMATION_FAMILY_MIN_MEMBERS = 3
# 家族键最大字符数：更长的公共前缀（近同名动作）不作为分组依据。
ANIMATION_FAMILY_MAX_KEY_LEN = 4
# 清单短于该长度就不分组：动作不多的角色保持平铺菜单，不多加一层。
ANIMATION_GROUP_MIN_ENTRIES = 24


def animation_menu_stay_open(pet) -> bool:
    """动作菜单是否连播（``anim_menu_stay_open``，默认 False = 点一次就收起）。

    False（默认）时动画叶子挂 ``closeOnTrigger``：点一次动作，用户要的是看动作，
    菜单必须让开。True 退回旧行为——菜单留在屏上，可以连点多个动作连播。
    只作用**触发动作播放**的叶子，设置类 / 开关类 / 子菜单一律不受影响。

    ``cfg`` 按面探测而不是直接取：菜单建造器的宿主可能是 duck-typed 替身
    （``__getattr__`` 兜底返回函数），拿不到真配置时按默认值处理。
    """
    getter = getattr(getattr(pet, "cfg", None), "get", None)
    if not callable(getter):
        return False
    return bool(getter("anim_menu_stay_open", False))


def _dash_family_key(name: str) -> str | None:
    """``余额-钱袋满溢`` 这类名字的分隔符前缀就是家族键。"""
    if "-" in name:
        key = name.split("-", 1)[0].strip()
        if key and key != ANIMATION_GROUP_RESIDUAL_LABEL:
            return key
    return None


def _prefix_family_key(name: str, names, minimum: int, max_key_len: int) -> str | None:
    """名字所属的前缀家族键（``吃*`` / ``原地*`` / ``放*``）。

    家族由**首字**划定（"吃西瓜 / 吃饺子 / 吃Token" 同族），键取该族全体成员的
    最长公共前缀：吃瓜/吃饺/吃Token 的公共前缀只有 "吃"，原地系列是 "原地"。
    这样组长短随素材自然变化，也不会把 "吃10号" 这类名字单独拆成一个 "吃1" 族。
    首字族凑不满 ``minimum`` 就是没有家族（"被吓一跳 / 被落叶淹没" 只有 2 项）。
    """
    head = name[:1]
    if not head or head == ANIMATION_GROUP_RESIDUAL_LABEL:
        return None
    family = [candidate for candidate in names if candidate.startswith(head)]
    if len(family) < minimum:
        return None
    key = head
    for length in range(2, max_key_len + 1):
        if len(name) < length:
            break
        prefix = name[:length]
        if not all(candidate.startswith(prefix) for candidate in family):
            break
        key = prefix
    return key


def group_animation_entries(entries) -> tuple[tuple[str, tuple[str, ...]], ...]:
    """把随机动作清单按前缀家族分组。

    返回 ``()`` 表示**不分组**（调用方按原清单平铺）：清单够短、整份清单共用
    同一个前缀（分组没有收益）、或前缀都凑不满 ``ANIMATION_FAMILY_MIN_MEMBERS``。
    不返回空家族，也不丢项——每个名字要么落在某个家族，要么落在「更多」，成员
    顺序与传入清单一致（只是换了一层菜单）。

    家族排序 = 成员数降序（同数保持首次出现顺序），「更多」永远最后。
    """
    names = [str(name) for name in entries]
    if len(names) < ANIMATION_GROUP_MIN_ENTRIES:
        return ()
    keys: dict[str, str | None] = {}
    for name in names:
        keys[name] = _dash_family_key(name) or _prefix_family_key(
            name, names, ANIMATION_FAMILY_MIN_MEMBERS, ANIMATION_FAMILY_MAX_KEY_LEN,
        )
    counts: dict[str, int] = {}
    for key in keys.values():
        if key is not None:
            counts[key] = counts.get(key, 0) + 1

    families: dict[str, list[str]] = {}
    residual: list[str] = []
    for name in names:
        key = keys[name]
        if key is None or counts.get(key, 0) < ANIMATION_FAMILY_MIN_MEMBERS:
            residual.append(name)
            continue
        families.setdefault(key, []).append(name)
    if len(families) + (1 if residual else 0) < 2:
        # 整份清单挤在同一个前缀里：拆开也不比原清单短，不做无收益的层级。
        return ()
    groups = [
        (label, tuple(members))
        for label, members in sorted(families.items(), key=lambda item: -len(item[1]))
    ]
    if residual:
        groups.append((ANIMATION_GROUP_RESIDUAL_LABEL, tuple(residual)))
    return tuple(groups)


def _populate_animation_category(
    submenu: QMenu, pet, entries, callback, leaf_role_icons: bool,
    close_on_trigger: bool,
) -> None:
    """首次展开动画分类子菜单时才填充动作，避免根菜单构建时遍历 91 个动画。"""
    if getattr(submenu, "_animation_populated", False):
        return
    submenu._animation_populated = True

    icon_actions = []
    lazy_actions = []
    for name in entries:
        action = submenu.addAction(QIcon(), name) if leaf_role_icons else submenu.addAction(name)
        # 触发一次动作播放的叶子：默认点完就收起整条菜单链（#221），想连播的
        # 用户把 anim_menu_stay_open 打开，退回"留在屏上连点"的旧行为。
        action.setProperty("closeOnTrigger", bool(close_on_trigger))
        connect_action(action, lambda name=name, callback=callback: callback(name))
        if leaf_role_icons:
            icon_actions.append((action, name))
            cached_loader = getattr(pet, "animation_icon_cached_image", None)
            cached = cached_loader(name) if callable(cached_loader) else None
            if cached is not None and not cached.isNull():
                action.setIcon(fitted_pet_pixmap_icon(submenu, QPixmap.fromImage(cached)))
            else:
                # A neutral loading glyph avoids a blank/jumping text
                # column while the representative frame is decoded.
                action.setIcon(vector_menu_icon(submenu, "loading"))
                lazy_actions.append((action, name))

    if not lazy_actions:
        return

    # Decoding dozens of WebM frames while the root menu is being constructed
    # made the very first right-click block for seconds. Load only the category
    # the user actually opens in a two-thread pool and keep completed icons on
    # their QAction for later opens.
    def refresh_cached_icons(
        submenu=submenu, icon_actions=tuple(icon_actions), pet=pet,
    ) -> None:
        """Only alter QAction geometry before show or after hide."""
        # aboutToHide 会排队 singleShot 刷新，菜单可能已销毁
        if shiboken6.isValid(submenu) is False:
            return
        if submenu.isVisible():
            return
        cached_loader = getattr(pet, "animation_icon_cached_image", None)
        if not callable(cached_loader):
            return
        for action, animation_name in icon_actions:
            image = cached_loader(animation_name)
            if image is not None and not image.isNull():
                action.setIcon(fitted_pet_pixmap_icon(submenu, QPixmap.fromImage(image)))

    def start_loading(submenu=submenu, lazy_actions=tuple(lazy_actions), pet=pet) -> None:
        loader = getattr(pet, "animation_icon_image", None)
        if not callable(loader):
            return
        if not hasattr(submenu, "_animation_icon_pending"):
            submenu._animation_icon_pending = list(lazy_actions)
            submenu._animation_icon_requested = set()

        def pump() -> None:
            while (
                len(submenu._animation_icon_workers) < 2
                and submenu._animation_icon_pending
            ):
                action, animation_name = submenu._animation_icon_pending.pop(0)
                if animation_name in submenu._animation_icon_requested:
                    continue
                submenu._animation_icon_requested.add(animation_name)
                launch(action, animation_name)

        def launch(action, animation_name) -> None:
            worker = _AnimationIconWorker(loader, animation_name)
            # 解码完成信号经队列投递到 GUI 线程的 applier 槽：
            # 菜单销毁时连接随 applier（submenu 子对象）自动断开。
            applier = _AnimationIconApplier(
                submenu, action, worker, pump, parent=submenu
            )
            worker.signals.ready.connect(applier.on_ready)
            submenu._animation_icon_workers.append(worker)
            submenu._animation_icon_pool.start(worker)

        pump()

    submenu.aboutToShow.connect(refresh_cached_icons)
    submenu.aboutToShow.connect(start_loading)
    submenu.aboutToHide.connect(
        lambda refresh=refresh_cached_icons, submenu=submenu: QTimer.singleShot(0, submenu, refresh)
    )
    pool = QThreadPool(submenu)
    pool.setMaxThreadCount(2)
    submenu._animation_icon_pool = pool
    submenu._animation_icon_workers = []
    # 首次填充时立刻启动解码；后续 aboutToShow 继续由连接驱动
    start_loading()


def build_animation_categories(
    menu: QMenu, pet, *, icons: bool, legacy_labels: bool = False,
    leaf_role_icons: bool = False,
) -> None:
    close_on_trigger = not animation_menu_stay_open(pet)
    categories = (
        ("待机", pet.idles, pet.switch_clip, False),
        ("转向", pet.turns, pet.switch_clip, False),
        ("移动", pet.moves, pet.trigger_move, False),
        ("点击回应", pet.clicks, pet.switch_clip, False),
        # 随机动作是唯一可能上百项的分类（实机 95 项，单列 1439px）：只有它按
        # 前缀再分一层，其余分类清单短，维持平铺。
        ("随机动作", pet.acts, pet.switch_clip, True),
    )
    for label, entries, callback, groupable in categories:
        if not entries:
            continue
        submenu = QMenu(f"动画 · {label}" if legacy_labels else label, menu)
        menu.addMenu(submenu)
        menu._owned_submenus = getattr(menu, "_owned_submenus", []) + [submenu]
        inherit_menu_style(menu, submenu)
        if icons:
            submenu.setIcon(vector_menu_icon(menu, "play"))
        # 首次展开该子菜单时才填充动作：根菜单构建不再遍历 91 个动画。家族
        # 子菜单在构建期就建好（此时样式/交互安装还没跑，它们能被递归覆盖到），
        # 动作同样延迟到各自 aboutToShow——只有用户真正展开的那一族才解码缩略图。
        # 宿主面可能是 duck-typed 替身（``__getattr__`` 兜底返回函数）：不是真
        # 序列就不分组，交给下面的平铺路径（与分组前逐位一致）。
        groups = ()
        if groupable and isinstance(entries, (list, tuple)):
            groups = group_animation_entries(entries)
        if groups:
            for family_label, family_entries in groups:
                family = add_submenu(submenu, family_label)
                family.aboutToShow.connect(
                    lambda s=family, e=family_entries, c=callback, l=leaf_role_icons,
                           p=pet, t=close_on_trigger:
                        _populate_animation_category(s, p, e, c, l, t)
                )
            continue
        submenu.aboutToShow.connect(
            lambda s=submenu, e=entries, c=callback, l=leaf_role_icons, p=pet,
                   t=close_on_trigger:
                _populate_animation_category(s, p, e, c, l, t)
        )


def build_speed_menu(menu: QMenu, pet, *, icons: bool = True) -> QMenu:
    submenu = add_submenu(menu, "播放速率", "speed" if icons else None)
    group = QActionGroup(submenu)
    group.setExclusive(True)
    for i in range(10, 21):
        value = i / 10.0
        action = submenu.addAction(f"{value:.1f}x")
        action.setCheckable(True)
        action.setChecked(abs(pet.playback_speed - value) < 0.01)
        group.addAction(action)
        connect_action(action, lambda value=value: pet.set_playback_speed(value))
    return submenu


def build_character_menu(menu: QMenu, pet, *, icons: bool = True) -> QMenu:
    submenu = add_submenu(menu, "切换角色", "character" if icons else None)
    group = QActionGroup(submenu)
    group.setExclusive(True)
    current = str(pet.cfg.get("character", catalog.DEFAULT_CHARACTER))
    for character_id in catalog.list_available_characters():
        alias_fn = getattr(pet.cfg, 'character_alias', None)
        alias = alias_fn(character_id) if callable(alias_fn) else ''
        label = alias or catalog.character_display_name(character_id)
        action = submenu.addAction(label)
        action.setCheckable(True)
        action.setChecked(character_id == current)
        group.addAction(action)
        action.setProperty("closeOnTrigger", True)
        connect_action(action, lambda character_id=character_id: pet.request_switch_character(character_id))
    # 角色显示名别名（空名恢复默认）
    rename = getattr(pet, "rename_character", None)
    if callable(rename):
        submenu.addSeparator()
        add_action(submenu, "重命名当前角色…", None, rename, close_on_trigger=True)
    return submenu


def add_proactive_menu(menu: QMenu, pet) -> None:
    """主动识屏二级菜单（仅 Windows 且有聊天/视觉能力时显示）。"""
    import sys as _sys
    if _sys.platform != 'win32':
        return
    if getattr(pet, 'on_open_chat', None) is None:
        return
    from ..proactive import effective_proactive_config

    sub = add_submenu(menu, "主动识屏", None)
    pro_cfg = effective_proactive_config(pet.cfg.get('proactive_screen', {}))

    def _toggle(text, checked, handler):
        act = sub.addAction(text)
        act.setCheckable(True)
        act.setChecked(bool(checked))
        act.toggled.connect(handler)
        return act

    _toggle('开启主动识屏', pro_cfg.get('enabled', False), pet.toggle_proactive_enabled)
    _toggle('鼠标穿透时仍允许主动识屏', pro_cfg.get('allow_when_mouse_through', True),
            lambda on: pet.set_proactive_option('allow_when_mouse_through', on))
    _toggle('触发前先兆提示', pro_cfg.get('pre_cue', True),
            lambda on: pet.set_proactive_option('pre_cue', on))
    _toggle('仅当我闲置时触发', pro_cfg.get('require_idle', False),
            lambda on: pet.set_proactive_option('require_idle', on))
    _toggle('dry-run 验证模式', pro_cfg.get('dry_run', False),
            lambda on: pet.set_proactive_option('dry_run', on))
    sub.addSeparator()
    open_settings = getattr(pet, 'on_open_modern_settings', None)
    if open_settings is not None:
        add_action(sub, '打开设置…', None, open_settings, close_on_trigger=True)


def add_quiet_mode_menu(menu: QMenu, pet, *, icons: bool = True) -> QMenu:
    """「免打扰」子菜单：四档时长 + 提前结束，开启期间显示剩余时间。

    为什么是一个 id 挂子菜单（同 ``add_harness`` / ``add_music_lyric_align``）：
    菜单模板与用户自己编排过的布局里只会出现 ``quiet_mode`` 这一个 id，新增 id
    对老布局不生效。

    状态是**进程级**的（``quiet_mode.shared_quiet_state()``）：多宠共享一份，
    任何一只的菜单开/关都作用于全部窗口——抑制本就是进程级的（issue #208）。
    时长档位取自 ``config.QUIET_MINUTE_CHOICES``，"默认"那一档由
    ``quiet_minutes_default`` 决定（设置页可改，这里只读不写）。
    """
    from .. import quiet_mode
    from ..config import QUIET_MINUTE_CHOICES

    state = quiet_mode.shared_quiet_state()
    active = state.manual_active()
    remaining = state.remaining_label()
    default_minutes = quiet_mode.normalize_quiet_minutes(
        pet.cfg.get("quiet_minutes_default", quiet_mode.DEFAULT_QUIET_MINUTES)
    )
    submenu = add_submenu(
        menu,
        f"免打扰 · 剩余 {remaining}" if active else "免打扰",
        "pause" if icons else None,
    )
    for minutes in QUIET_MINUTE_CHOICES:
        label = quiet_mode.quiet_duration_label(minutes)
        if minutes == default_minutes:
            label += "（默认）"
        if active and minutes == state.started_minutes():
            label += " · 进行中"
        add_action(
            submenu, label, None,
            lambda minutes=minutes: quiet_mode.shared_quiet_state().start(minutes),
            close_on_trigger=True,
        )
    submenu.addSeparator()
    end_action = add_action(
        submenu,
        f"提前结束（剩余 {remaining}）" if active else "提前结束",
        None,
        lambda: end_quiet_mode(pet),
        close_on_trigger=True,
    )
    # 未在免打扰中时置灰而不是整条隐藏：菜单结构不随状态变形（同
    # registry._music_lyric_configured 的取舍），用户也能一眼看到入口在哪。
    end_action.setEnabled(bool(active))
    return submenu


def end_quiet_mode(pet) -> list[str]:
    """提前结束免打扰并汇报暂存提醒（返回本轮待汇报的提醒文案）。

    与到期、退出全屏走**同一条交付链**：``end_now``（只停止计时）→
    ``take_report``（取快照）→ 汇报成功才 ``ack_report``（排空）。交付失败时
    暂存原样保留，交给进程级轮询表在目标可用时重试——先排空再丢弃是不允许的。

    走 ``pet`` 的宿主面汇报：菜单建造器的 pet 既可能是 PetWindow 也可能是
    overlay 的 SpriteMenuFacade，两者都有气泡/提醒面（由 window_alerts 择优）。
    """
    from .. import quiet_mode
    from .. import window_alerts

    state = quiet_mode.shared_quiet_state()
    state.end_now()
    items = state.take_report()
    if items is None:
        # 仍处于（全屏等效）免打扰：此刻不该汇报，退出全屏时由轮询表接手。
        return []
    if not items:
        state.ack_report()
        return []
    if window_alerts.report_quiet_summary(pet, items):
        state.ack_report()
    return list(items)


def add_agent_link_menu(menu: QMenu, pet) -> None:
    """Agent 联动二级菜单（4 个 Agent 独立开关 + 自定义 Agent 三级子菜单 + 气泡提醒选项，失败/拒绝自动回滚勾选）。"""
    sub = add_submenu(menu, "Agent 联动", None)
    agent_cfg = dict(pet.cfg.get('agent_link', {}))
    for agent_key, agent_label in (
        ('dsh', 'DeepSeek Harness (DSH)'),
        ('claude', 'Claude Code'),
        ('codex', 'Codex'),
        ('cursor', 'Cursor'),
        ('opencode', 'OpenCode'),
    ):
        act = sub.addAction(agent_label)
        act.setCheckable(True)
        act.setChecked(bool(agent_cfg.get(agent_key, False)))
        if agent_key == 'codex':
            act.setToolTip('首次开启自动安装插件；安装后请在 Codex 中信任 hooks。关闭时卸载桌宠安装的插件。')
            manager = getattr(pet, 'agent_link_manager', None)
            if manager is not None and 'codex' in getattr(manager, '_install_pending', {}):
                act.setText('Codex（正在处理…）')
                act.setEnabled(False)
        act.toggled.connect(lambda on, k=agent_key, a=act: pet.toggle_agent_link(k, on, a))
    # 自定义联动 Agent（config.json 的 agent_link.custom_agents，只读监听）：
    # 收进三级子菜单，避免用户配了多个自定义通道后把联动菜单撑长。
    custom_items = [
        item for item in (agent_cfg.get('custom_agents') or [])
        if str(item.get('key') or '')
    ]
    if custom_items:
        custom_sub = add_submenu(sub, "自定义联动 Agent", None)
        for item in custom_items:
            key = str(item.get('key'))
            act = custom_sub.addAction(str(item.get('name') or key))
            act.setCheckable(True)
            act.setChecked(bool(agent_cfg.get(key, False)))
            act.toggled.connect(lambda on, k=key, a=act: pet.toggle_agent_link(k, on, a))


def build_size_menu(menu: QMenu, pet, *, icons: bool = True) -> QMenu:
    submenu = add_submenu(menu, "大小", "size" if icons else None)
    group = QActionGroup(submenu)
    group.setExclusive(True)
    for scale in catalog.SCALE_STEPS:
        px = int(round(catalog.CANVAS_W * scale))
        action = submenu.addAction(f"{px}px")
        action.setCheckable(True)
        action.setChecked(abs(pet.scale - scale) < 0.02)
        group.addAction(action)
        connect_action(action, lambda scale=scale: pet.change_scale(scale))
    return submenu


def add_drag_physics(menu: QMenu, pet, *, icons: bool = True):
    action = add_action(menu, "拖动物理", "physics" if icons else None)
    action.setCheckable(True)
    action.setChecked(pet.drag_physics)
    action.toggled.connect(lambda enabled, pet=pet: pet.set_drag_physics(enabled))
    return action


def add_return_corner(menu: QMenu, pet, *, icons: bool = True):
    return add_action(menu, "回到右下角", "corner" if icons else None, pet.go_default_corner)


def add_hide_pet(menu: QMenu, pet, *, icons: bool = True):
    # close_on_trigger：隐藏后菜单随之关闭，避免菜单悬空无法找回桌宠
    return add_action(menu, "隐藏桌宠", "hide" if icons else None, pet.hide, close_on_trigger=True)


def add_look_screen(menu: QMenu, pet, *, icons: bool = True):
    callback = getattr(pet, "on_look_screen", None)
    if callback is None:
        return None
    return add_action(menu, "看看屏幕", "screen" if icons else None, callback, close_on_trigger=True)


def network_info_menu_enabled(pet) -> bool:
    """「网络信息」入口的开关（``network_info_menu_enabled``，默认开）。

    开关真值只有一份：注册表的 ``enabled`` 与本模块建造器写的 tooltip 共用它，
    避免"设置里关掉了、菜单里还在"这种两套真值。
    """
    cfg = getattr(pet, "cfg", None)
    if cfg is None:
        return True
    return bool(cfg.get("network_info_menu_enabled", True))


#: 入口关掉时的说明（注册表的 ``disabled_reason`` 与建造器写的 tooltip 共用这一份，
#: 口径见 CONTEXT.md 的 Runtime Disabled：保留位置、置灰、并解释原因）。
NETWORK_INFO_DISABLED_REASON = "已在设置里关闭「网络信息」菜单入口"


def add_network_info(menu: QMenu, pet, *, icons: bool = True):
    """「网络信息」入口：**能力**看回调，**开关**看配置，两者语义不同。

    - 回调不存在（纯桌宠变体）＝ 能力不可用 → 整条不出现；
    - 开关关掉 ＝ 运行时停用 → 保留位置、置灰并写上说明（不消失、也不点了没反应）。
      置灰在建造器里落地（任何调用方——布局渲染或直调——拿到的都是同一份 reason），
      ``populate`` 之后还会按 ``enabled_ids`` 再置一次灰（幂等）。
    """
    callback = getattr(pet, "on_network_info", None)
    if callback is None:
        return None
    action = add_action(
        menu, "网络信息", "web" if icons else None, callback, close_on_trigger=True)
    if not network_info_menu_enabled(pet):
        action.setEnabled(False)
        action.setToolTip(NETWORK_INFO_DISABLED_REASON)
    return action


#: 「截图」入口名。置灰理由的真值在 ``pet/screen_capture/reasons.py``
#: （动作条「翻译」按钮的 tooltip 与结果浮层状态行共用一份）。
SCREEN_CAPTURE_LABEL = "截图提问"


def add_screen_capture_menu(menu: QMenu, pet, *, icons: bool = True):
    """「截图」= 单一**直接**菜单项（不进子菜单）：点了就进框选。

    交互模型（2026-10-14 实机反馈后重做）：菜单只负责**开始框选**；框完松手，
    覆盖层动作条给出「提取文字 / 翻译 / 截图问答 / 复制图像 / 取消」由用户当场选。
    原来还套一层只装一个叶子的子菜单——实机反馈：一个选项没必要（平铺直出）。

    截图栈（控制器/覆盖层/编排件）仍只有真触发时才装载——本函数不 import
    ``screen_capture.actions``，装载点在动作回调里（``actions_for``）。
    """
    callback = getattr(pet, "on_screen_capture", None)
    if not callable(callback):
        return None  # 宿主没接这套面（纯桌宠变体）= 能力不可用，整组不出现
    return add_action(menu, SCREEN_CAPTURE_LABEL, "size" if icons else None,
                      callback, close_on_trigger=True)


def add_balance(menu: QMenu, pet, *, icons: bool = True):
    callback = getattr(pet, "on_show_balance", None)
    if callback is None:
        return None
    return add_action(menu, "DeepSeek 余额", "balance" if icons else None, lambda: callback(pet), close_on_trigger=True)


def add_no_move(menu: QMenu, pet, *, icons: bool = True):
    action = add_action(menu, "不移动", "pause" if icons else None)
    action.setCheckable(True)
    action.setChecked(pet.no_move)
    action.toggled.connect(lambda enabled, pet=pet: pet.set_no_move(enabled))
    return action


def add_on_top(menu: QMenu, pet, *, icons: bool = True):
    action = add_action(menu, "窗口置顶", "pin" if icons else None)
    action.setCheckable(True)
    action.setChecked(bool(pet.cfg.get("on_top", True)))
    action.toggled.connect(lambda enabled, pet=pet: pet.set_on_top(enabled))
    return action


def add_autostart(menu: QMenu, pet=None, *, icons: bool = True):
    action = add_action(menu, "开机自启", "autostart" if icons else None)
    action.setCheckable(True)
    action.setChecked(autostart_mod.is_enabled())
    def toggle(enabled: bool) -> None:
        autostart_mod.set_enabled(enabled)
        if pet is not None:
            pet.cfg.set("autostart_wanted", bool(enabled))
            pet.cfg.save()

    action.toggled.connect(toggle)
    return action


def add_spawn_pet(menu: QMenu, pet):
    callback = getattr(pet, "on_spawn_pet", None)
    if callback is None:
        return None
    if str(menu.property("menuStyle") or "") == "modern":
        icon = pet_avatar_menu_icon(menu, pet)
    else:
        icon = QIcon()
    action = menu.addAction(icon, "生小肥鱼")
    action.setProperty("closeOnTrigger", True)
    connect_action(action, callback)
    return action


def add_clear_spawned_pets(menu: QMenu, pet, *, icons: bool = True):
    """右键菜单快捷入口：退出所有小肥鱼（slot-N），设置与数据保留。"""
    callback = getattr(pet, "on_clear_spawned_pets", None)
    if callback is None:
        return None
    return add_action(
        menu,
        "退出子肥鱼",
        "clear" if icons else None,
        callback,
        close_on_trigger=True,
    )


def add_golden_spin(menu: QMenu, pet, *, icons: bool = True):
    """右键菜单快捷入口：让桌宠原地逆时针旋转一圈。"""
    callback = getattr(pet, "trigger_golden_spin", None)
    return add_action(
        menu,
        "黄金回旋",
        "play" if icons else None,
        callback,
        close_on_trigger=True,
    )


def add_edge_probe(menu: QMenu, pet, *, icons: bool = True):
    """右键菜单开关：开启/关闭拖到屏幕边缘后的自动探头。"""
    action = add_action(menu, "边缘探头", "corner" if icons else None)
    action.setCheckable(True)
    action.setChecked(bool(pet.cfg.get("edge_probe_enabled", False)))
    action.toggled.connect(
        lambda enabled, pet=pet: pet.set_edge_probe_enabled(enabled)
    )
    return action


def _dialog_parent(pet):
    """模态对话框的父窗口。

    ``pet`` 在窗口路径下就是 QWidget（PetWindow）；在 sprite 路径下是
    ``SpriteMenuFacade`` 这样的 duck-typed 宿主，不能当 QMessageBox 的 parent，
    此时用宿主自报的 ``dialog_parent``（overlay 窗）。PetWindow 没有该属性 →
    原样返回自己，窗口路径逐位不变。
    """
    return getattr(pet, "dialog_parent", None) or pet


def confirm_destructive_action(pet, *, title: str, text: str, informative: str = "",
                               confirm_label: str, cancel_label: str = "取消") -> bool:
    """破坏性动作的确认框：返回用户是否点了确认按钮。

    与 dsh 重启/停止的确认框同款构造（``harness_launcher._confirm_harness_stop``）：
    Warning 图标 + 明确的「确认 X / 取消」两个按钮 + ``exec()``。

    **默认按钮 = 取消**（与那处相反，故收口在这里写明口径）：退出主宠这类动作在
    右键菜单里离「设置」「退出」只有几像素，回车/连点落到确认上就不可逆了；默认落
    在取消上，误触的代价是一次多余的点击。

    调用方必须让菜单项 ``close_on_trigger``（回调延迟到菜单关闭后执行）：macOS
    原生菜单跟踪会话里模态框会被 AppKit 抑制，与"设置对话框首次点击无反应"同源。
    """
    from PySide6.QtWidgets import QMessageBox

    box = QMessageBox(_dialog_parent(pet))
    box.setWindowTitle(title)
    box.setIcon(QMessageBox.Icon.Warning)
    box.setText(text)
    if informative:
        box.setInformativeText(informative)
    confirm = box.addButton(confirm_label, QMessageBox.ButtonRole.AcceptRole)
    cancel = box.addButton(cancel_label, QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(cancel)
    box.exec()
    return box.clickedButton() is confirm


def add_harness(menu: QMenu, pet, *, icons: bool = True):
    """DeepSeek Harness 子菜单：启动 / 重启 / 停止。

    为什么用子菜单而不是三个平级项：菜单默认布局（pet/menu_templates/
    modern-default-v1.json）与用户自己编排过的布局里只有 ``harness`` 这一个 id，
    新增 id 对老布局不生效；子菜单挂在既有 id 上，老用户的菜单立刻拿到完整生命周期
    入口。

    重启/停止是破坏性动作（可能结束你自己在终端里跑着的 dsh），带确认框；启动
    保持原语义（复用本机实例并打开页面）。
    """
    start_icon = "harness" if icons else None
    submenu = add_submenu(menu, "DeepSeek Harness", start_icon)

    def _launch(action: str = "start", target: str = "auto") -> None:
        # 父窗口在**点击时**解析：宿主（facade）可能在菜单关闭后被回收，
        # 这里只读它自报的 dialog_parent 面，不缓存对象。
        launch_harness_gui(_dialog_parent(pet), action=action, target=target)

    # 所有动作都 close_on_trigger：菜单先关闭、回调延迟到菜单关闭后执行——
    # 重启/停止的确认框是模态框，macOS 原生菜单跟踪会话中弹模态框会被
    # AppKit 抑制（与设置对话框首次点击无反应同源）。
    # 启动分双目标（2026-10）：web 界面 / 桌面端界面；重启/停止只作用于
    # web 服务进程（桌面端是用户的主力应用，桌宠绝不终止它）。
    add_action(submenu, "启动 dsh web 界面", start_icon,
               lambda: _launch("start", "web"),
               close_on_trigger=True)
    add_action(submenu, "启动桌面端界面", start_icon,
               lambda: _launch("start", "desktop"),
               close_on_trigger=True)
    add_action(
        submenu, "重启服务", "play" if icons else None,
        lambda: _launch("restart"),
        close_on_trigger=True,
    )
    add_action(
        submenu, "停止服务", "quit" if icons else None,
        lambda: _launch("stop"),
        close_on_trigger=True,
    )
    return submenu


def _music_gated() -> bool:
    """音乐模块未激活 → True（右键音乐动作的**回调**闸）。

    菜单层已按激活快照投影（``registry.available_ids``，九条动作都在
    ``features.music.menu_action_ids`` 里），这里兜底绕过菜单的直达回调：
    旧布局残留、用户编排里的陈旧节点、外部调用都不该在模块停用后还能建控制器
    或起后台线程。``is_active`` 在快照未冻结（测试/工具）时恒真，行为与旧版一致。
    """
    from .. import feature_activation

    if feature_activation.is_active("music"):
        return False
    log.info("音乐模块未激活，忽略右键音乐动作")
    return True


def _music_controller(pet):
    """取歌词控制器。**不**在这里创建——仅打开菜单不该装一套定时器。

    必须校验类型而不是只判断 None：宿主可能用 ``__getattr__`` 兜底返回任意
    对象（测试替身就这么干），那样会把一个无关对象当成控制器。
    """
    from ..music.lyric_controller import MusicLyricController

    controller = getattr(pet, "_music_lyric", None)
    return controller if isinstance(controller, MusicLyricController) else None


def _skip_track(pet, direction: str) -> bool:
    if _music_gated():
        return False
    controller = _music_controller(pet)
    if controller is None:
        return False
    return controller.skip_track(direction)


def music_mode_active(pet) -> bool:
    """音乐模式是否在跑（含"临时退出"状态），供菜单勾选态与可用性判断。"""
    controller = _music_controller(pet)
    if controller is not None:
        return controller.music_mode_active()
    return bool(pet.cfg.get("music_lyric_enabled", False))


def set_music_mode(pet, on: bool) -> None:
    """临时进入/退出音乐模式（仅本次运行，不写设置）。"""
    if _music_gated():
        return
    controller = _music_controller(pet)
    if controller is None:
        installer = getattr(pet, "install_music_lyric", None)
        if callable(installer):
            controller = installer()
    enable = getattr(controller, "set_music_mode_enabled", None)
    if callable(enable):
        enable(on)


class _MusicLaunchBridge(QObject):
    """「打开播放器」worker → GUI 的气泡桥（与 proactive 的 _WatcherBridge 同款）。

    后台线程只 emit；槽在 GUI 线程执行——跨线程直接碰 Qt 控件是未定义行为。
    真实宿主（PetWindow 是 QObject）走 Qt 父子关系保活；非 QObject 宿主（测试
    替身/最小外壳）没有 parent，由 :func:`_music_launch_bridge` 留一份强引用，
    等 queued 信号投递完成后在槽里自删。
    """

    notice = Signal(str)
    # worker 收工（成功/失败都发）：桥的两种保活都要靠它收口——非 QObject 宿主
    # 的强引用集合、真实宿主窗口上的子对象，否则每次点击都会多留一份。
    _worker_done = Signal()

    def __init__(self, pet) -> None:
        parent = pet if isinstance(pet, QObject) else None
        super().__init__(parent)
        self._pet = pet
        self.notice.connect(self._show_notice)
        self._worker_done.connect(self._release)

    @Slot(str)
    def _show_notice(self, text: str) -> None:
        _LAUNCH_BRIDGES.discard(self)
        show = getattr(self._pet, "show_bubble", None)
        if callable(show):
            try:
                show(text, duration_ms=6000)
            except Exception:
                log.debug("播放器提示气泡展示失败", exc_info=True)

    @Slot()
    def _release(self) -> None:
        """worker 收工：摘掉强引用并把对象交还 Qt（成功路径也会走到这里）。"""
        _LAUNCH_BRIDGES.discard(self)
        self.deleteLater()


# 非 QObject 宿主的强引用兜底：没有 Qt parent，不留住就被 GC 掉、queued 信号丢失。
_LAUNCH_BRIDGES: set = set()


def _music_launch_bridge(pet) -> _MusicLaunchBridge:
    bridge = _MusicLaunchBridge(pet)
    if bridge.parent() is None:
        _LAUNCH_BRIDGES.add(bridge)
    return bridge


def _launch_player_and_play(player_key: str, pet) -> None:
    """打开指定播放器并尽量让它开始播放。

    "自动播放"是**尽力而为**：已经在跑的播放器可以直接发播放指令；刚启动的
    需要等它初始化并出现在 SMTC 里（可能几秒），所以起一个后台线程轮询，
    等到了就发播放。等不到也不报错——播放器自己是否自动续播由它决定，
    这不是我们能控制的。

    **路径解析（find_player）也在 worker 线程里**：缓存冷时它要浅扫目录（实测
    6s+），以前这段跑在 GUI 线程，点一下菜单就卡住。找不到时不静默 return，
    而是经 queued 信号回 GUI 线程弹气泡说明可以在配置文件里手动指定路径。
    """
    from ..music import players as music_players, now_playing

    if _music_gated():
        # 模块未激活：连桥与 worker 线程都不建（启动播放器/轮询 SMTC 全不做）
        return
    label = music_players.player_label(player_key)
    manual = ""
    paths_cfg = pet.cfg.get("music_player_paths", {})
    if isinstance(paths_cfg, dict):
        manual = str(paths_cfg.get(player_key, "") or "")
    bridge = _music_launch_bridge(pet)

    def worker() -> None:
        try:
            exe = music_players.find_player(player_key, manual)
            if not exe:
                # 桥的生命周期挂在宿主窗口上：浅扫期间窗口被销毁（切换形象/退出）
                # 时它已经是个死对象，emit 会抛 RuntimeError（同 agent_link 的
                # 防护写法）。这里必须吞掉——否则 daemon 线程以未捕获异常收尾。
                try:
                    bridge.notice.emit(f"找不到{label}：可在 设置 → 桌宠 → 音乐关联 里指定它的程序位置")
                except RuntimeError:
                    pass
                return
            exe_name = os.path.basename(exe)
            # 先给已经在跑的会话发播放指令：这条路径是确定的。
            if now_playing.play_session_for(exe_name):
                return
            # 没在跑：启动它，然后轮询等它出现在 SMTC 里。
            try:
                if sys.platform == "win32":
                    os.startfile(exe)  # noqa: S606 - 路径来自受控的播放器搜索
                else:
                    QProcess.startDetached(exe, [])
            except Exception:
                return
            for _ in range(10):          # 最多等 10 秒
                time.sleep(1.0)
                if now_playing.play_session_for(exe_name):
                    return
        finally:
            # 成功路径原先在这里直接 return，桥既不出队也不回收；无论成败都收口。
            try:
                bridge._worker_done.emit()
            except RuntimeError:
                pass  # 宿主窗口已销毁，桥随之一并没了

    threading.Thread(target=worker, name="music-launch", daemon=True).start()


def _run_off_main(callable_) -> None:
    """把播放器控制丢到后台线程执行。

    now_playing 的控制接口内部走 asyncio.run() 调 WinRT，实测会在主线程
    永久阻塞（窗口未响应）。菜单回调都在主线程，必须挪走。
    """
    threading.Thread(target=callable_, name="music-menu", daemon=True).start()


def _run_music_off_main(callable_) -> None:
    """音乐菜单回调的线程入口：模块未激活时连后台线程都不起（媒体控制整条不建）。"""
    if _music_gated():
        return
    _run_off_main(callable_)


def add_music_pause(menu: QMenu, pet, *, icons: bool = True):
    """音乐子菜单：暂停 / 播放。"""
    from ..music import now_playing

    return add_action(
        menu, "让人家歇一会儿嘛（暂停 / 播放）", "pause" if icons else None,
        lambda: _run_music_off_main(now_playing.toggle_play_pause), close_on_trigger=True,
    )


def add_music_next(menu: QMenu, pet, *, icons: bool = True):
    """音乐子菜单：切歌。"""
    return add_action(
        menu, "给主人换一首（切歌）", "play" if icons else None,
        lambda: _run_music_off_main(lambda: _skip_track(pet, "next")), close_on_trigger=True,
    )


def add_music_prev(menu: QMenu, pet, *, icons: bool = True):
    """音乐子菜单：切到上一首。"""
    return add_action(
        menu, "人家想再听刚才那首（上一首）", "play" if icons else None,
        lambda: _run_music_off_main(lambda: _skip_track(pet, "previous")), close_on_trigger=True,
    )


def add_music_quit(menu: QMenu, pet, *, icons: bool = True):
    """音乐子菜单开关：临时退出音乐模式（勾选=已退出）。"""
    action = add_action(menu, "人家今天不唱了（退出音乐模式）", "stop" if icons else None)
    action.setCheckable(True)
    action.setChecked(not music_mode_active(pet))
    action.toggled.connect(lambda off: set_music_mode(pet, not off))
    return action


def _align_lyric(pet, kind: str) -> bool:
    """执行一次歌词对齐；返回是否真的动作了。

    **纯内存操作，必须留在 GUI 线程**（其余音乐菜单项走 ``_run_off_main`` 是因为
    它们要调 WinRT）。这里只改 LyricTracker 的本地时钟基准，微秒级。
    """
    if _music_gated():
        return False
    controller = _music_controller(pet)
    if controller is None:
        return False
    if kind == "start":
        return controller.resync_to_start()
    if kind == "prev":
        return controller.resync_to_line(-1)
    if kind == "next":
        return controller.resync_to_line(1)
    if kind == "back5":
        return controller.nudge(-5.0)
    if kind == "fwd5":
        return controller.nudge(5.0)
    return False


#: 「歌词对齐」不可用时的说明（``add_music_lyric_align`` 写的 tooltip 与注册表的
#: ``disabled_reason`` 共用这一份；口径见 CONTEXT.md 的 Runtime Disabled：保留
#: 位置、置灰、并解释原因）。
MUSIC_ALIGN_DISABLED_REASON = "当前不需要手动对齐（播放器会上报进度，或还没有歌词）"


def music_align_ready(pet) -> bool:
    """「歌词对齐」是否可用：只有位置靠本地时钟估算时才有意义。

    播放器报了真实进度（Chrome / QQ 音乐）时位置本来就跟着快进走，手动对齐
    只会跟真值打架；还没有歌词可对齐时同理。**必须 isinstance 校验**——宿主
    可能用 ``__getattr__`` 兜底返回任意对象（测试替身就这么干），只判 None 会
    把无关对象当成控制器（同 ``_music_controller`` 的教训）。

    放在共享模块而不是注册表里：判定与置灰必须同源，否则会出现
    "入口在、点了没反应"的死项（B8 的历史形态是旧版菜单直调建造器绕过注册表；
    旧版菜单 2026-10-08 已退役，口径不变）。
    """
    controller = _music_controller(pet)
    return bool(controller.align_available()) if controller is not None else False


def add_music_lyric_align(menu: QMenu, pet, *, icons: bool = True):
    """「歌词对齐」子菜单：纠正快进 / 中途开始播放造成的歌词错位。

    为什么是"一个 id 挂子菜单"而不是五个平级项：与 ``add_harness`` 同一个理由——
    默认布局与用户自己编排过的布局里只会出现 ``music_lyric_align`` 这一个 id。

    为什么用「上一句 / 下一句」做主手柄：用户听到的是"现在唱这句"，而不是
    "现在第几秒"，按行步进比按秒微调快得多；±5 秒留给最后一点偏差。

    实测依据（2026-09-21）：网易云音乐完全不上报播放进度，歌词只能按本地时钟
    估算，快进或中途开始播放后必然错位，且没有任何自动信号可利用——所以给用户
    一个一次点击就能对准的入口，是这个功能唯一诚实的做法。

    **置灰也在本建造器里落地**：门只写在注册表上，任何绕过注册表的调用方都会
    拿到一条点了没反应的入口。``populate`` 之后会按 ``enabled_ids`` 再置一次灰
    （同一份 reason，幂等）。
    """
    icon = "play" if icons else None
    submenu = add_submenu(menu, "歌词对齐", icon)
    add_action(submenu, "回到开头（现在这句算开头）", icon,
               lambda: _align_lyric(pet, "start"), close_on_trigger=True)
    add_action(submenu, "上一句", icon,
               lambda: _align_lyric(pet, "prev"), close_on_trigger=True)
    add_action(submenu, "下一句", icon,
               lambda: _align_lyric(pet, "next"), close_on_trigger=True)
    add_action(submenu, "后退 5 秒", icon,
               lambda: _align_lyric(pet, "back5"), close_on_trigger=True)
    add_action(submenu, "前进 5 秒", icon,
               lambda: _align_lyric(pet, "fwd5"), close_on_trigger=True)
    if not music_align_ready(pet):
        action = submenu.menuAction()
        action.setEnabled(False)
        action.setToolTip(MUSIC_ALIGN_DISABLED_REASON)
    return submenu


def _music_player_builder(player_key: str):
    """生成"打开某播放器并播放"的 builder（四个播放器共用一套逻辑）。

    新增播放器只加一个 ``add_music_open_<key>`` 转发（见文件末尾四个），路径解析、
    冷缓存乐观启用、后台预热、负缓存置灰、手填路径失效提示全部由这里一次性给出。
    """

    def build(menu: QMenu, pet, *, icons: bool = True):
        from ..music import players as music_players

        if _music_gated():
            # 模块未激活：不建条目、不读路径缓存、不起后台浅扫（菜单层已投影过滤，
            # 这是绕过菜单直调建造器的兜底）
            return None
        label = music_players.player_label(player_key)
        manual = ""
        paths_cfg = pet.cfg.get("music_player_paths", {})
        if isinstance(paths_cfg, dict):
            manual = str(paths_cfg.get(player_key, "") or "")
        # 只读缓存的查询：菜单构建在 GUI 线程，缓存冷时扫目录会冻住菜单
        # （实测 6.4s 全在 MainThread）。冷缓存按"乐观可用"处理，扫描交给
        # 幂等的后台预热；真正的确定态最迟下次开菜单拿到。
        # 路径本身这里不用：点击路径会在 worker 线程里再解析一次（权威结果）。
        state, _path = music_players.cached_player(player_key, manual)
        missing = state == music_players.CACHED_MISSING
        action = add_action(
            menu, f"打开{label}给主人放歌", None,
            (lambda: _launch_player_and_play(player_key, pet)) if not missing else None,
            close_on_trigger=True,
        )
        if missing:
            action.setEnabled(False)
            action.setToolTip(f"找不到{label}：可在 设置 → 桌宠 → 音乐关联 里指定它的程序位置")
        elif state == music_players.CACHED_COLD:
            music_players.warm_cache_async(player_key, manual)
        return action

    return build


def add_music_open_netease(menu: QMenu, pet, *, icons: bool = True):
    return _music_player_builder("netease")(menu, pet, icons=icons)


def add_music_open_qqmusic(menu: QMenu, pet, *, icons: bool = True):
    return _music_player_builder("qqmusic")(menu, pet, icons=icons)


def add_music_open_kugou(menu: QMenu, pet, *, icons: bool = True):
    return _music_player_builder("kugou")(menu, pet, icons=icons)


def add_music_open_soda(menu: QMenu, pet, *, icons: bool = True):
    return _music_player_builder("soda")(menu, pet, icons=icons)


def add_agent_cost(menu: QMenu, pet, *, icons: bool = True):
    """右键菜单开关：本轮结束时显示消费金额。

    与设置页开关同一份配置（``agent_cost_enabled``），两边实时联动。
    """
    action = add_action(menu, "显示本轮消费", "balance" if icons else None)
    action.setCheckable(True)
    action.setChecked(bool(pet.cfg.get("agent_cost_enabled", False)))

    def _on_toggled(on: bool) -> None:
        pet.cfg.set("agent_cost_enabled", bool(on))
        pet.cfg.save()
        # 让窗口按新配置同步控制器（与设置页走同一条刷新路径）。
        for name in ("refresh_pet_settings", "sync_optional_services"):
            fn = getattr(pet, name, None)
            if callable(fn):
                try:
                    fn()
                except Exception:
                    pass

    action.toggled.connect(_on_toggled)
    return action


class _MemoryTrimBridge(QObject):
    """「回收内存」worker → GUI 线程的气泡桥（保活口径同 ``_MusicLaunchBridge``）。

    worker 只 emit；槽在 GUI 线程执行——跨线程直接碰 Qt 控件是未定义行为。
    真实宿主（PetWindow 是 QObject）走 Qt 父子关系保活；非 QObject 宿主（测试
    替身/最小外壳）没有 parent，由 :data:`_MEMORY_TRIM_BRIDGES` 留一份强引用，
    等 queued 信号投递完成后在槽里自删，避免每次点击多留一份。
    """

    notice = Signal(str)
    _worker_done = Signal()

    def __init__(self, pet) -> None:
        parent = pet if isinstance(pet, QObject) else None
        super().__init__(parent)
        self._pet = pet
        self.notice.connect(self._show_notice)
        self._worker_done.connect(self._release)

    @Slot(str)
    def _show_notice(self, text: str) -> None:
        _MEMORY_TRIM_BRIDGES.discard(self)
        show = getattr(self._pet, "show_bubble", None)
        if callable(show):
            try:
                show(text, duration_ms=6000)
            except Exception:
                log.debug("回收内存气泡展示失败", exc_info=True)

    @Slot()
    def _release(self) -> None:
        """worker 收工（成功/失败都发）：摘掉强引用并把对象交还 Qt。"""
        _MEMORY_TRIM_BRIDGES.discard(self)
        self.deleteLater()


_MEMORY_TRIM_BRIDGES: set = set()


def _memory_trim_bridge(pet) -> _MemoryTrimBridge:
    bridge = _MemoryTrimBridge(pet)
    if bridge.parent() is None:
        _MEMORY_TRIM_BRIDGES.add(bridge)
    return bridge


def trigger_memory_trim(pet) -> None:
    """「回收内存」动作：后台线程收拾其它进程工作集，完成后气泡汇报。

    过滤与安全红线全在 ``pet/memory_trim.py``（纯逻辑，零 Qt）；这里只做三件事：
    读配置、起 worker、把结果经队列信号送回 GUI 线程弹气泡。**菜单点击路径必须
    立刻返回**——枚举几百个进程 + 逐个 OpenProcess 放在 GUI 线程跑就是卡菜单。

    ``system_tools`` 模块未激活：本函数是「回收内存」的唯一执行落点（注册表投影
    之外还有 builder 直调与旧布局残留两条路），不读配置、不起 ``memory-trim``
    线程、不枚举进程。
    """
    if not feature_activation.is_active("system_tools"):
        log.info("系统工具模块未激活，忽略「回收内存」请求")
        return
    from .. import memory_trim

    cfg = getattr(pet, "cfg", None)
    min_size_mb = (
        cfg.get("mem_min_size_mb", memory_trim.DEFAULT_MIN_SIZE_MB)
        if cfg is not None else memory_trim.DEFAULT_MIN_SIZE_MB
    )
    skip_foreground = bool(cfg.get("mem_skip_foreground", True)) if cfg is not None else True
    bridge = _memory_trim_bridge(pet)

    def _report(report) -> None:
        try:
            bridge.notice.emit(report.text)
        except RuntimeError:
            pass  # 宿主窗口已销毁（切换形象/退出），桥随之一并没了
        finally:
            try:
                bridge._worker_done.emit()
            except RuntimeError:
                pass

    memory_trim.run_in_background(
        _report, min_size_mb=min_size_mb, skip_foreground=skip_foreground,
    )


def add_memory_trim(menu: QMenu, pet, *, icons: bool = True):
    """「回收内存」：回收**其它**进程的工作集（仅 Windows，非 Windows 整条不显示）。

    自己 / 当前前台 / 自己的子进程 / 系统关键进程一律不碰（见
    ``pet/memory_trim.py`` 的模块 docstring）；这里只负责菜单项与触发。

    ``system_tools`` 模块未激活整条不建——与平台门同址（builder 是注册表投影
    之外的第二条入口，只挡注册表会被直调路径绕过）。
    """
    from .. import memory_trim

    if not feature_activation.is_active("system_tools"):
        return None
    if not memory_trim.is_supported():
        return None
    return add_action(
        menu, "回收内存", "clear" if icons else None,
        lambda: trigger_memory_trim(pet), close_on_trigger=True,
    )


def open_deepseek_web() -> bool:
    return bool(QDesktopServices.openUrl(QUrl(DEEPSEEK_WEB_URL)))


def add_deepseek_web(menu: QMenu, *, icons: bool = True):
    return add_action(menu, "打开网页版 DeepSeek", "web" if icons else None, open_deepseek_web, close_on_trigger=True)


def add_quit(menu: QMenu, pet, *, icons: bool = True):
    return add_action(menu, "退出", "exit" if icons else None, pet.request_quit, close_on_trigger=True)
