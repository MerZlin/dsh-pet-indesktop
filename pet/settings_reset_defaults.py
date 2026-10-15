# -*- coding: utf-8 -*-
"""「恢复默认设置」入口（设置页侧栏按钮 → 二次确认 → 整体重置）。

只做**整体重置**这一个粒度，但有两个**作用域**，各自一个动作、各自一份确认文案：
「本宠恢复默认」只重置这只桌宠的每宠偏好（保位置/身份/active/历史）；「全局恢复默认」
只重置所有桌宠共用的全局设置（保每宠档案/位置/active/凭据）。按分页重置在没有"用户
到底改坏了哪一页"的信息时帮不上小白用户（issue #220 的原声场景），也把交互压到
按钮 + 一次确认。

作用域**不由实例名推断**：判据是"这次重置落在哪个配置面上"——本宠动作落在
``SlotRoutedConfig``（闸在 slot 文件），全局动作落在 ``global_config_of(config)``
（显式全局身份，``config.json``）。slot0 从 G1 起有显式身份，按"实例名非空 = 子宠"
猜会把主宠页的全局动作接错面（2026-10-10 终审 A04：产品入口全部变成 slot-N，
全局 reset 一度无 UI 可达）。

唯一保留项是 chat 域的密钥类字段（含非默认 provider 的"只装凭据"容器），清单与理由见
``pet/config.py`` 的 ``CHAT_SECRET_FIELDS``；全局动作的确认文案必须逐项写明保留了什么
（用户按"不可撤销"决策时看到的就是这段文字）。本宠动作的保留面大得多、也更简单：除了
那只宠自己的独立设置，别的都不动。

重置完成后**关窗**而不是原地刷新：本对话框的控件是一次性从配置构造的（没有"按配置
重建全部控件"的路径），重置后窗口里全是旧值，而关闭路径会把全部控件整表写回
（``_write_config``）——不闸住就把刚写的默认值静默覆盖回去。所以走 ``_saved_via_button``
（既有的"已由按钮完成落盘"闸门）+ ``accept()``：运行中的桌宠由既有的
config watcher → ``reload()`` + ``_apply_external_config_change()`` 链应用默认值。

落盘之外还有两件"只落盘做不到"的收尾（缺一即确认文案里的承诺不成立）：

- **系统登录项按新值收敛**（:func:`sync_system_autostart`）：开机自启是 OS 侧状态，
  配置回默认不会自己关掉注册表/LaunchAgent 里的条目；与对话框既有的
  ``_apply_autostart`` 同一条通道，只是在设置进程里按新值调一次。**只有全局作用域**
  才碰它——本宠作用域一个全局键都没有重置，去写 OS 就是把用户没改的登录项改掉。
- **运行态对齐**（:func:`_request_runtime_alignment`）：**两个作用域两条语义**（G1b）。
  全局请求刷新服务，本宠请求CMD_PROFILE_RESET同步偏好/角色并保身体锚。
  运行消费者使用同一档案和generation，关闭目标只更新会话角色而不复活。

实现拆出 ``modern_settings_dialog.py`` 的原因与 settings_music / settings_file_interpret
同规：主对话框只留一行接线（该文件有行数预算）。
"""

from __future__ import annotations

import logging

from PySide6.QtWidgets import QMessageBox, QPushButton

from . import autostart as autostart_mod
from .context_menus.icons import vector_widget_icon

__all__ = [
    "RESET_SCOPE_GLOBAL",
    "RESET_SCOPE_SLOT",
    "create_reset_button",
    "on_reset_clicked",
    "build_reset_confirm_box",
    "confirm_reset",
    "reset_scope_is_slot",
    "resolve_reset_scope",
    "reset_config_for",
    "sync_system_autostart",
    "reload_runtime_config",
]

#: 两个作用域的规范名（与 ``SlotRoutedConfig`` 的闸、与文案一一对应）。
RESET_SCOPE_SLOT = "slot"
RESET_SCOPE_GLOBAL = "global"

RESET_BUTTON_TEXT = "恢复默认设置…"
RESET_TOOLTIP = "恢复影响所有桌宠的全局设置；保留每宠档案、位置、活跃状态与 AI 密钥。"
#: slot 页上的第二个动作：全局 reset 在这里也要可达（否则从产品 UI 根本走不到它）。
RESET_GLOBAL_BUTTON_TEXT = "恢复全局默认…"
RESET_TITLE = "恢复默认设置"
RESET_HEADLINE = "将重置全局设置为默认值。"
RESET_KEPT = (
    "保留：AI 对话密钥——各服务商的 API Key 与视觉模型 Key；每个自定义服务商只保留"
    "密钥本身，地址、模型等其它设置会恢复默认。系统钥匙串里的条目不会删除。"
)
RESET_BODY = (
    "全局功能开关、聊天连接与显示设置恢复出厂默认；开机自启按默认关闭。\n"
    "保留所有桌宠的档案、角色、尺寸、位置、活跃状态及会话历史。\n"
    "设置窗口将随即关闭。"
)
# 子宠页（slot 实例）专属文案（R08）：那边重置的只有这只宠自己的档案。
# 主宠页那套"形象 / 系统提示词 / 自定义台词与角色别名 / 外观与功能开关统统恢复出厂"
# 的承诺在这里**不成立**——那些键都写在主配置、一个都不会被重置；反过来，"AI 密钥
# 保留"那条在这里也不用说（压根没碰主配置）。两支的措辞结构对齐（保留项 + 其余项），
# 用户看到的两段话读起来是同一种话术，只有作用域不同。
#
# G1b：本宠 reset **保留位置**（"回到默认落位"是独立操作，见规范 §五），文案必须跟着
# 改——旧文案写的"位置立即回默认落位"是把一段不成立的承诺交到用户手里，正是 R08 的
# 同一种错（承诺 ≠ 事实）。
RESET_SLOT_TOOLTIP = (
    "把这只桌宠的独立设置（尺寸、速度、音效、气泡与角色）恢复为默认值；位置保持不变。"
)
RESET_SLOT_HEADLINE = "将重置这只桌宠的独立设置为默认值（不可撤销）。"
RESET_SLOT_KEPT = (
    "保留：当前所在位置，以及语音报时、灵动岛、AI 对话等全局设置，还有其它桌宠的全部设置。"
)
RESET_SLOT_BODY = (
    "只重置这只桌宠自己的那份设置：尺寸、速度、音效、气泡与角色。\n"
    "当前所在位置不变；需要它回默认落位时，用右键菜单/托盘的「回到右下角」。\n"
    "形象与素材类设置需重启后生效。\n"
    "设置窗口将随即关闭。"
)
# 按钮文案自写而不用 StandardButton.Yes：产品没装 Qt 中文翻译（仓库内无
# QTranslator），标准按钮在中文界面上仍显示 "Yes"，破坏性操作不该让人猜。
RESET_CONFIRM_LABEL = "恢复默认设置"
RESET_CANCEL_LABEL = "取消"


def reset_scope_is_slot(host) -> bool:
    """宿主的配置面是不是**每宠（slot）作用域**——即 ``reset_to_defaults`` 被闸在
    slot 文件上。

    判据 = 配置面是不是 ``SlotRoutedConfig``：只有它把 ``reset_to_defaults`` 闸在
    slot 文件上，文案必须跟着**那道闸**走，而不是跟着实例名走——作用域由路由决定，
    不由实例名决定。按实例名判会把子宠页文案挂到不该挂的进程上，恰好重演 R08——
    文案承诺的作用域 ≠ 实际重置的范围。

    传入的宿主没有配置面（文案断言用的 ``None`` / 测试替身）一律按全局口径。
    轻量：只 import 一个零 Qt 的模块，且它本来就随设置窗口一起被导入
    （``modern_settings_dialog`` 用它的 ``route_settings_config``），不新增任何
    运行期依赖。
    """
    config = getattr(host, "config", None)
    if config is None:
        return False
    from .settings_slot_config import SlotRoutedConfig

    return isinstance(config, SlotRoutedConfig)


def resolve_reset_scope(host, scope=None) -> str:
    """这次点的是哪个作用域：显式 ``scope`` 优先，缺省按宿主配置面判（老调用面兼容）。"""
    if scope in (RESET_SCOPE_SLOT, RESET_SCOPE_GLOBAL):
        return scope
    return RESET_SCOPE_SLOT if reset_scope_is_slot(host) else RESET_SCOPE_GLOBAL


def reset_config_for(host, scope=None):
    """该作用域实际被重置的配置面；拿不到时 ``None``。

    本宠 → 宿主手上的路由视图（写它自己的 slot 文件）；全局 → 路由视图背后的那份
    全局配置（``config.json``，显式全局身份）。**不新建第三个 Config 实例**：全局面
    必须就是设置窗正在显示的那一份，否则两份内存视图会在落盘时互相盖回去。
    """
    config = getattr(host, "config", None)
    if config is None:
        return None
    if resolve_reset_scope(host, scope) == RESET_SCOPE_SLOT:
        return config
    from .settings_slot_config import global_config_of

    return global_config_of(config)


def create_reset_button(host, layout) -> QPushButton:
    """在设置窗侧栏底部创建「恢复默认设置…」入口（slot 页上另加全局动作）。

    放侧栏而不是设置页行：它是命令不是偏好项（docs/SETTINGS-CHANGE-GATES.md
    的准入清单明确排除命令类），与同侧的「保存并退出」同属应用级操作。
    提示文案按作用域分（子宠页不是"全部设置"）。

    **slot 页必须同时给出全局动作**（2026-10-10 终审 A04）：设置页所有产品入口都带
    ``--instance slot-N``（含 slot0），只留一个本宠按钮时全局 reset 在 UI 上**完全不可达**
    ——它已经在实现里活着，用户却只能靠手工命令行 ``--settings`` 才够得着。两个动作复用
    两个既有实现、两份既有文案，不新增设置项、不改版式（同区两个命令按钮，与侧栏其余
    按钮同风格）。
    """
    slot_page = reset_scope_is_slot(host)
    button = QPushButton(RESET_BUTTON_TEXT, host)
    button.setObjectName("resetDefaults")
    button.setIcon(vector_widget_icon(button, "retry", 16))
    button.setToolTip(RESET_SLOT_TOOLTIP if slot_page else RESET_TOOLTIP)
    button.setAutoDefault(False)
    button.setDefault(False)
    button.clicked.connect(lambda: on_reset_clicked(host))
    host.reset_defaults_btn = button
    layout.addWidget(button)
    if slot_page:
        glob = QPushButton(RESET_GLOBAL_BUTTON_TEXT, host)
        glob.setObjectName("resetGlobalDefaults")
        glob.setIcon(vector_widget_icon(glob, "retry", 16))
        glob.setToolTip(RESET_TOOLTIP)
        glob.setAutoDefault(False)
        glob.setDefault(False)
        glob.clicked.connect(
            lambda: on_reset_clicked(host, scope=RESET_SCOPE_GLOBAL))
        host.reset_global_defaults_btn = glob
        layout.addWidget(glob)
    return button


def build_reset_confirm_box(parent, scope=None) -> tuple[QMessageBox, QPushButton]:
    """构造二次确认框（不 exec，便于断言文案与按钮语义）。

    文案按**作用域**分两支（见 :func:`resolve_reset_scope`）：全局 = 全部设置
    （原样不变），本宠 = 只这只宠的独立设置。两支的按钮语义完全相同。
    """
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Warning)
    box.setWindowTitle(RESET_TITLE)
    if resolve_reset_scope(parent, scope) == RESET_SCOPE_SLOT:
        box.setText(RESET_SLOT_HEADLINE)
        box.setInformativeText(f"{RESET_SLOT_KEPT}\n\n{RESET_SLOT_BODY}")
    else:
        box.setText(RESET_HEADLINE)
        box.setInformativeText(f"{RESET_KEPT}\n\n{RESET_BODY}")
    confirm = box.addButton(RESET_CONFIRM_LABEL, QMessageBox.ButtonRole.DestructiveRole)
    cancel = box.addButton(RESET_CANCEL_LABEL, QMessageBox.ButtonRole.RejectRole)
    # 破坏性操作：默认按钮与 Esc 都落在取消上，回车不会误触重置。
    box.setDefaultButton(cancel)
    box.setEscapeButton(cancel)
    return box, confirm


def confirm_reset(parent, scope=None) -> bool:
    """弹确认框并返回用户是否选择重置（取消/关框一律 False）。"""
    box, confirm = build_reset_confirm_box(parent, scope)
    box.exec()
    return box.clickedButton() is confirm


def sync_system_autostart(host, *, config=None) -> None:
    """把系统登录项收敛到重置后的值（``autostart_wanted`` 默认关闭）。

    与对话框既有的 ``_apply_autostart`` 同一条通道、同一个进程（系统自启是
    OS 侧状态，不需要桌宠运行期配合）；差别只在这里没有"用户勾选值"可比，
    直接按重置后的配置值收敛。系统侧已是目标值时不做任何写入。

    ``config`` 显式给出被重置的全局配置面（全局动作从 slot 页发起时必须传，
    否则读到的会是被路由出去的每宠面）。缺省按作用域取：本宠作用域**不碰 OS**
    ——它一个全局键都没重置，写登录项就是把用户没改过的东西改掉；全局作用域读
    宿主手上的配置面。

    **开机自启对任意宠都生效**（``autostart_wanted`` 是全局键，系统登录项只有一份），
    所以这里不再按"实例名非空"拒绝——那条旧判据的本意是"子宠页控件置灰"，而控件
    已经不置灰了（2026-10-10 终审 A04-b）。
    """
    if config is None:
        if reset_scope_is_slot(host):
            return
        config = getattr(host, "config", None)
    if config is None:
        return
    wanted = bool(config.get("autostart_wanted", False))
    if autostart_mod.is_enabled() == wanted:
        return
    if autostart_mod.set_enabled(wanted) is False:
        QMessageBox.warning(
            host,
            "开机自启设置失败",
            "写入开机自启失败：可能被安全软件拦截。\n"
            "可在设置页的「开机自启」开关里重试，或检查安全软件/系统优化工具的拦截记录。",
        )


def reload_runtime_config(*configs) -> bool:
    """重置级运行态对齐**之前**，把运行期配置从磁盘拉齐。返回是否真的重载过。

    为什么必须在对齐前做（B1）：对齐的落点要经 ``cfg.save()`` **整表**落盘（legacy
    是 ``win.go_default_corner`` → ``save_position``，overlay 是退出时的
    ``save_position``）。而设置进程是"先写默认值、再写对齐指令"两步目录事件，主
    进程的事件处理顺序是「先消费指令（立刻对齐并落盘）、后按 300ms 去抖排 reload」
    ——对齐那一刻运行期那份 Config 还停在重置前的旧值，一次整表落盘就把刚落盘的
    默认值全部覆盖回去（``no_move`` / ``pet_opacity`` / ``stream_capture_mode`` …），
    重置被静默抵消。

    ``reload()`` 本身是既有的"外部变更合并"口（键清单见 ``Config.reload``），与
    watcher 随后那一拍同源，重复调用幂等。多个候选里同一份配置只重载一次（生产
    路径 ``win.cfg`` 与 ``shell.config`` 常常就是同一个对象）。

    无 ``reload`` 接口的假配置/测试替身跳过（对齐照跑，行为不比修复前更差）。
    """
    reloaded = False
    seen: set[int] = set()
    for config in configs:
        if config is None or id(config) in seen:
            continue
        seen.add(id(config))
        reload_fn = getattr(config, "reload", None)
        if not callable(reload_fn):
            continue
        try:
            reload_fn()
        except Exception:
            logging.exception("恢复默认设置：运行态配置重载失败，对齐可能被旧值覆盖")
            continue
        reloaded = True
    return reloaded


def _request_runtime_alignment(host, scope=None) -> None:
    """全局reset刷新服务；本宠reset同步偏好/角色并保位置，关闭目标不复活。

    全局作用域一律 ``CMD_APPLY_SETTINGS_RESET`` + ``target=None``：全局 reset 影响
    所有桌宠的运行态，落点不挑哪一只——按宿主身份回填 target 会让"从 slot0 页点的
    全局 reset"变成"只刷 slot0"（而 slot0 可能压根没在活跃清单里，消费端直接跳过，
    刷新静默不发生）。
    """
    resolved = resolve_reset_scope(host, scope)
    slot_scope = resolved == RESET_SCOPE_SLOT
    parent = host.parentWidget()
    callback = getattr(parent, "on_settings_reset", None)
    if callable(callback) and not slot_scope:
        callback()
        return
    from .overlay_settings_command import (
        CMD_APPLY_SETTINGS_RESET,
        CMD_PROFILE_RESET,
        slot_from_instance_id,
        write_command,
    )

    if not slot_scope:
        if not write_command(host.config.dir, CMD_APPLY_SETTINGS_RESET, target=None):
            logging.warning("恢复默认设置：全局刷新指令写盘失败，等待配置监听或重启应用")
        return
    target = slot_from_instance_id(host.config.instance_id)
    if target is None:
        logging.warning("恢复默认设置：本宠身份里没有 slot 编号，跳过运行态同步指令")
        return
    if not write_command(host.config.dir, CMD_PROFILE_RESET, target=target):
        logging.warning(
            "恢复默认设置：运行态同步指令写盘失败，这只桌宠要等下次刷新才会用上新默认值")


def on_reset_clicked(host, scope=None) -> bool:
    """按钮处理：确认 → 重置并落盘 → 运行态对齐 → 关窗。返回是否真的重置成功。

    ``scope`` 由侧栏的两个动作各自绑定（本宠 / 全局）；缺省按宿主配置面判（兼容既有
    调用面与测试）。取消或落盘失败都返回 False 且对磁盘零副作用（调用方据此决定关不关窗）。
    """
    resolved = resolve_reset_scope(host, scope)
    if not confirm_reset(host, resolved):
        return False
    target = reset_config_for(host, resolved)
    if target is None or not target.reset_to_defaults():
        QMessageBox.warning(
            host,
            "恢复默认设置失败",
            "配置未能写入磁盘，设置未重置。\n\n配置路径：" + str(
                getattr(target, "path", getattr(getattr(host, "config", None), "path", "未知"))),
        )
        return False
    logging.info("设置已恢复默认（%s）：%s", resolved, target.path)
    if resolved == RESET_SCOPE_GLOBAL:
        sync_system_autostart(host, config=target)
    _request_runtime_alignment(host, resolved)
    # 关窗但闸住整表写回（控件里是重置前的旧值，见模块 docstring）；
    # flag 语义由对话框自己拥有。
    host._close_after_external_write()
    return True
