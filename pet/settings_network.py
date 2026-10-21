# -*- coding: utf-8 -*-
"""设置页「灵动岛指标 / 网络信息」的控件创建 / 行装配 / 保存（issue #172 + P1 Slice C）。

modern_settings_dialog.py 行数预算刚校准到零余量，所以本功能的控件与行全部在
本模块构建，对话框只做三处接线：import 1 行、岛页挂「网络信息」小节 3 行、保存
委托 1 行（菜单域那处是既有「内容与布局」行的追加，净 0；该文件预算已按实测
2420 校准并带日期注释）。

归属（SETTINGS-CHANGE-GATES 的"归属唯一"）：

- ``island_metrics``（岛上的附加指标段的有序列表）只属于「桌面组件 · 桌面胶囊
  （灵动岛）」——岛的信息槽显示什么，只在岛这一处决策。旧键
  ``island_network_enabled`` 已废弃为写入来源（归一化阶段单向迁移进列表），
  本页只读列表、只写列表。
- ``network_probe_interval_seconds`` 是网络段的采样间隔，归属同上（唯一天然
  属主）；
- ``network_info_menu_enabled`` 只属于「菜单」——它决定右键菜单里那一项显不显示，
  与岛上的指标段无关；两处不复制对方的状态。

依赖显隐：四个指标行跟着「岛开关 × 信息槽开关」走，间隔行只跟网络开关走。
「信息槽内容 / 自定义短文本」两行**不受**网络开关影响——网络段自 P1 Slice D1 起
是普通指标段，不再接管信息槽（"网络接管"旧语义已删），info_mode 始终生效。
"""
from __future__ import annotations

import threading

from PySide6.QtCore import QSize, QTimer
from PySide6.QtWidgets import QLineEdit, QPushButton

from . import feature_activation
from . import fps_permission, net_monitor
from .island import extension_host as island_extension_host, segments as island_segments
from .config import (
    GAME_FPS_PROCESS_MAX_CHARS,
    _clean_game_fps_process,
    _clean_game_fps_target_mode,
    _clean_island_metrics,
)
from .settings_widgets import (
    BrowserDoubleSpinBox,
    ModernSelect,
    SettingRow,
    ToggleSwitch,
)

#: 探测间隔的展示口径（真值口径在 net_monitor.clamp_interval，不在这里重写一份）
_MIN_INTERVAL = net_monitor.MIN_INTERVAL_SECONDS
_MAX_INTERVAL = net_monitor.MAX_INTERVAL_SECONDS
_DEFAULT_INTERVAL = net_monitor.DEFAULT_INTERVAL_SECONDS

#: 指标行：``(段 id, 行 objectName 键, 行 label, 行 hint)``。
#: **本元组的顺序就是 ``_METRIC_ORDER``：新勾选项写回 ``island_metrics`` 的追加序**
#: （唯一来源，下面从它派生）。复选框不表达顺序（拖动排序留给后续版本），
#: 几个布尔态只能折成一个列表，那就给"新勾的"定死一序——否则"先勾内存再勾网络"
#: 会让保存后的段序随勾选先后漂移，用户看不出为什么岛上的顺序变了。要自定义顺序
#: 先改配置文件的列表序（保存保留它，见 ``save_network_settings``）、或等排序 UI。
#: **新行一律追加在末尾**（P3 的 gpu 就是这么加的）：把新行插进中间会让老用户
#: 保存一次就把自己的段序重排一次——那是用户看不见的破坏。
#: 行 label 一律含「灵动岛指标」，CPU / 内存各一行（设置搜索按 label 命中）。
_METRIC_ROWS = (
    (
        island_segments.NETWORK,
        "island_network",
        "灵动岛指标 · 网络",
        "在岛上加一段 ↓下行 / ↑上行速率与网络延迟（信息栏内容另有其行，不受本开关影响）。"
        "速率监测不产生额外流量，延迟检测每次约 0.16 KB。"
        "默认关闭——开启后会持续检测。",
    ),
    (
        island_segments.CPU,
        "island_cpu",
        "灵动岛指标 · CPU",
        "在岛上加一段 CPU 总占用百分比（不逐核心）。读数由共享的后台采样线程取，"
        "1 秒一次、绝不进动画帧；关掉即不采样（零开销）。",
    ),
    (
        island_segments.MEM,
        "island_mem",
        "灵动岛指标 · 内存",
        "在岛上加一段内存占用百分比。与 CPU 共用同一条后台采样线程和同一份系统"
        "快照（不同系统的「已用」口径以系统自身为准）；关掉即不采样。",
    ),
    (
        island_segments.GPU,
        "island_gpu",
        "灵动岛指标 · GPU",
        "在岛上加一段 GPU 占用百分比。读的是「最忙的那个 GPU 引擎」的占用，不是整卡"
        "总算力（两者不是一回事，所以数值与任务管理器里的整卡百分比可能不同）。"
        "Windows 上由共享后台采样线程 1 秒一次读取；关掉、折叠进 +N、岛隐藏或收成"
        "停靠细条时都不采样。第一拍之前显示 --（不是 0）。系统不支持时这一段不占位，"
        "只在展开卡片里说明原因；显卡名与引擎只在卡片里。",
    ),
    (
        island_segments.FPS,
        "island_fps",
        "灵动岛指标 · 游戏帧率",
        "在岛上加一段游戏的 Present FPS——游戏每秒真正呈现到屏幕上的帧数。"
        "它不是桌宠动画的帧率（那是素材与渲染节拍），也不是显卡占用推算出来的"
        "（三者互不相等，不能互相替代）。只在 Windows x64 上有这条采集能力"
        "（随包的是 x64 采集程序）：其他系统或架构上"
        "这一段不占位，展开卡片里说明原因。默认关闭、开启后才启动采集：采集走系统级"
        "性能事件接口，首次使用可能需要在系统里授予相应权限（没授权时卡片里写真实原因，"
        "不拿假读数顶上），它有一定开销（多少以实机测量为准）。采集谁由下面两行决定。"
        "第一拍之前显示 --（不是 0）。",
    ),
)

#: 写回顺序（见 :data:`_METRIC_ROWS` 的注释）——**内置行那一半**；第三方组件的行
#: 追加在它之后（见 :func:`metric_order`）。
_METRIC_ORDER = tuple(row[0] for row in _METRIC_ROWS)

#: 流量成本文案（单次探测在网字节 → 每天 MB；进设置行文案）
_KB_PER_ROUND = net_monitor.PROBE_BYTES_PER_ROUND / 1024.0
#: Qt 的 QWIDGETSIZE_MAX（解除基类固定宽度的写法，同 dynamic_island 先例）
_QWIDGETSIZE_MAX = 16777215

# ---------------------------------------------------------------- 游戏帧率段（fps）
#: 目标模式选项：``(配置值, 用户说法)``。顺序 = 下拉里的顺序，默认项排第一。
_FPS_TARGET_MODES = (
    ("foreground", "跟随前台程序"),
    ("process", "指定程序"),
)
#: 「指定程序」模式的配置值（判断输入框该不该可编辑时用它，不写字面量）。
_FPS_PROCESS_MODE = "process"
#: 跟随行：只在 fps 指标开启时才显示的目标设置（与「延迟探测间隔」跟网络开关同规）。
_FPS_FOLLOW_KEYS = ("game_fps_target_mode", "game_fps_process", "game_fps_permission")
#: 授权行按钮在各权限状态下的文字（``fps_permission.STATUS_*`` → 文案）。
_FPS_PERMISSION_BUTTON = "授予采集权限…"
_FPS_PERMISSION_TEXT = {
    fps_permission.STATUS_ACTIVE: "已具备采集权限",
    fps_permission.STATUS_PENDING: "已授权，注销或重启电脑后生效",
    fps_permission.STATUS_MISSING: _FPS_PERMISSION_BUTTON,
    fps_permission.STATUS_UNSUPPORTED: "无法判定权限，点此尝试授权…",
}
#: 后台授权结果的轮询间隔（毫秒）：GUI 线程只读结果，不等子进程。
_FPS_PERMISSION_POLL_MS = 200
#: 进程级授权任务（thread / job），与设置窗口生命周期解耦。
_FPS_GRANT_JOB: dict = {}
#: 程序名输入的测宽样本：按它给控件量最小宽度，长 exe 名在放大字体下不裁字。
_FPS_PROCESS_SAMPLE = "a-long-game-executable.exe"
#: 程序名输入在两种模式下的提示（置灰时必须给出原因，不让用户猜）。
_FPS_PROCESS_TOOLTIP = (
    "填可执行文件名，例如 game.exe（名字里带空格照填，如 My Game.exe）。"
    "只按名字匹配进程，不会启动或执行这个程序。"
)
_FPS_PROCESS_DISABLED_TOOLTIP = (
    "当前是「跟随前台程序」：这一项不生效。要固定采集某个程序，先把上面的采集目标"
    "改成「指定程序」。"
)

class _FpsModeSelect(ModernSelect):
    """宽度跟着**字体度量**走的模式下拉（只改尺寸，不碰绘制与 QSS）。

    基类把宽度钉死（``setFixedWidth(132)``），而它自绘的文本区不带省略号——窗口/系统
    字体一放大，「跟随前台程序」就被裁字（真实 windows 平台插件下 1.5 倍字体实测需要
    154px）。这里把基类现成的 :meth:`ModernSelect.suggestedWidth`（装得下最长选项，
    含左内缩与 chevron 的量法）接进 Qt 的尺寸协议：``sizeHint`` 随字体走，
    ``preferred_inline_width`` 让 :class:`SettingRow` 在窄窗口里按同一度量换成上下布局。
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # 解除基类的固定宽度：宽度交给 sizeHint（字体驱动），下限是当前实测与基类默认值
        self.setMinimumWidth(max(self.width(), self.suggestedWidth()))
        self.setMaximumWidth(_QWIDGETSIZE_MAX)

    def preferred_inline_width(self) -> int:
        return int(self.suggestedWidth())

    def sizeHint(self) -> QSize:  # noqa: N802
        base = super().sizeHint()
        return QSize(max(int(self.suggestedWidth()), base.width()), base.height())


def _metric_attr(metric_id: str) -> str:
    """指标开关在对话框上的属性名（network 沿用既有 ``island_network_check``）。"""
    return f"island_{metric_id}_check"


def _extension_rows() -> tuple[tuple[str, str, str, str], ...]:
    """第三方组件行（``(段 id, 行键, 行 label, 行 hint)``）。

    行**与内置指标行同层级、同风格**（同一个 ``SettingRow`` 装配、同一个"灵动岛指标 ·"
    前缀、同一套显隐依赖），只是 label 与 hint 取自组件自己的声明。顺序 = 声明清单的
    扫描序（用户目录在前、目录名排序），与"谁先注册"无关——两个进程因此得到同一份
    行序，保存后的列表序也不会随进程内的注册顺序漂移。
    """
    rows: list[tuple[str, str, str, str]] = []
    for declaration in island_extension_host.declarations():
        segment_id = declaration.segment_id
        label = declaration.label or segment_id
        provider_note = (
            f"组件暂缺：找不到 {declaration.provider}，这份偏好会保留，"
            "但当前不采样、也不在岛上显示（装回来后自动恢复）。"
            if not declaration.provider_present else
            f"数据来自本机组件 {declaration.provider}（{declaration.source or '来源由组件声明'}）。")
        rows.append((
            segment_id,
            f"island_{segment_id}",
            f"灵动岛指标 · {label}",
            f"第三方组件「{label}」提供的指标段。{provider_note}"
            "单位、最坏读数样本与告警阈值都由组件自己声明，本应用只负责岛上排版、"
            "卡片详情与告警判定；关掉即不采样。",
        ))
    return tuple(rows)


def metric_rows() -> tuple[tuple[str, str, str, str], ...]:
    """全部指标行 = 内置段行 + 声明清单里的组件行（**顺序就是写回序**）。"""
    return _METRIC_ROWS + _extension_rows()


def metric_order() -> tuple[str, ...]:
    """写回 ``island_metrics`` 的典范序：内置在前，组件按声明序追加在末尾。

    新勾选的按它追尾（已有且仍勾选的按原序在前）——所以"先勾哪个"不影响落盘顺序，
    而"这一轮进程里谁先注册"更不影响：组件行来自**声明清单**（静态 metadata），
    不来自运行期注册表。
    """
    return tuple(row[0] for row in metric_rows())


def _checked_metrics(dialog) -> list:
    """当前配置里的指标列表（走 config 的同一份归一化：脏值/未知 id 的口径只有一处）。"""
    return _clean_island_metrics(dialog.config.get("island_metrics"))


def _traffic_hint(interval_seconds: float) -> str:
    """按间隔算出"每天大约多少 MB"——文案里的数字跟着真实间隔走，不写死。"""
    per_day_mb = _KB_PER_ROUND * (86400.0 / max(_MIN_INTERVAL, interval_seconds)) / 1024.0
    return f"{per_day_mb:.1f} MB/天"


def create_network_controls(dialog) -> None:
    """在对话框上创建本功能的控件（幂等；行由下面两个 build_* 装配）。

    建控件的同时记下**本次表单的指标 id 快照**（``_island_metric_controls``）：
    可信目录里的组件可能在窗口打开之后被装上 / 卸掉，而控件只在这一次建。保存必须
    按**这份快照**收集值——按保存当刻的清单收集会去取一个从没建过的控件
    （``getattr`` 抛 AttributeError，整条保存链在写入之前断掉），或者把"表单里根本
    没有的 id"当成"用户没勾"而静默删掉用户的偏好。
    """
    if getattr(dialog, "island_network_check", None) is None:
        # 指标开关读的是 island_metrics 的成员关系（列表没有的顺序不表达，
        # 复选框只表达"在不在"）。行集合含第三方组件（声明清单驱动，见 _extension_rows）
        selected = _checked_metrics(dialog)
        controlled = metric_order()
        for metric_id in controlled:
            toggle = ToggleSwitch(dialog)
            toggle.setChecked(metric_id in selected)
            setattr(dialog, _metric_attr(metric_id), toggle)
        dialog._island_metric_controls = tuple(controlled)

        dialog.network_probe_interval_spin = BrowserDoubleSpinBox(dialog)
        dialog.network_probe_interval_spin.setRange(_MIN_INTERVAL, _MAX_INTERVAL)
        dialog.network_probe_interval_spin.setDecimals(1)
        dialog.network_probe_interval_spin.setSingleStep(0.5)
        dialog.network_probe_interval_spin.setSuffix(" 秒")
        dialog.network_probe_interval_spin.setValue(net_monitor.clamp_interval(
            dialog.config.get("network_probe_interval_seconds", _DEFAULT_INTERVAL)))
        _fit_spin_to_content(dialog.network_probe_interval_spin)
        dialog.network_probe_interval_spin.valueChanged.connect(
            lambda _value: _sync_interval_hint(dialog))

    if getattr(dialog, "network_info_menu_check", None) is None:
        dialog.network_info_menu_check = ToggleSwitch(dialog)
        dialog.network_info_menu_check.setChecked(
            bool(dialog.config.get("network_info_menu_enabled", True)))

    # 这几个键都是**进程级**（不在 PER_SLOT_SETTING_KEYS，采样器/岛/右键菜单一律读
    # 主配置）：子宠页保存由设置进程的配置路由直写主配置
    # （pet/settings_slot_config.py），改了真生效——此前按死开关口径置灰，现摘除。


def _form_metric_ids(dialog) -> tuple[str, ...]:
    """本次表单**实际可编辑的**指标 id = 表单快照 ∩ 真的有控件。

    - 快照（``_island_metric_controls``）由 :func:`create_network_controls` 建控件时
      记下，所以它不随窗口打开之后的清单变动而变；
    - 再按"真的有控件"过一遍：快照里有、控件却没了（不该发生，但**保偏好**永远比
      猜"用户没勾"安全），以及调用方自己拼控件、没有快照的兜底路径，都收敛到同一条
      规则——"这一行这次真的可编辑"。

    没有快照时按当刻清单回落，**不按"清单里现在有什么"判**（那正是 A09 的根因）。
    """
    snapshot = getattr(dialog, "_island_metric_controls", None)
    candidates = tuple(snapshot) if snapshot else metric_order()
    return tuple(metric_id for metric_id in candidates
                 if getattr(dialog, _metric_attr(metric_id), None) is not None)


def _fit_spin_to_content(spin) -> None:
    """按字体度量给足宽度，并解除基类的固定宽度。

    基类 ``BrowserDoubleSpinBox`` 固定 92px：本项最长值 "60.0 秒" 在默认字体下
    正好占满行编辑区（本机实测 72/72px，零余量），放大字体 / High DPI 就会把数字
    裁掉。这里改成"宽度随内容与字体度量增长"（可视化验收要求：放大字体不得裁切）。
    """
    text = f"{spin.maximum():.{spin.decimals()}f}{spin.suffix()}"
    needed = spin.fontMetrics().horizontalAdvance(text) + 44  # 箭头区 + 内边距
    spin.setMinimumWidth(max(needed, spin.width()))
    spin.setMaximumWidth(_QWIDGETSIZE_MAX)


def _interval_hint(dialog) -> str:
    """间隔行的文案（"每天多少流量"跟着当前值算，不写死）。"""
    return (
        f"延迟检测间隔（{_MIN_INTERVAL:g}–{_MAX_INTERVAL:g} 秒）。间隔越短反应越快、流量越多："
        f"当前约 {_traffic_hint(_spin_value(dialog))}（随设置实时更新）。"
        "检测目标是固定的境内服务（阿里云），不是你的访问网站。"
    )


def _sync_interval_hint(dialog) -> None:
    """间隔一变就把文案重算一遍（数字必须跟真实间隔一致）。"""
    row = dialog.findChild(SettingRow, "settingRow_network_probe_interval")
    if row is None or not hasattr(row, "hint_label"):
        return
    row.hint_label.setText(_interval_hint(dialog))


def _spin_value(dialog) -> float:
    spin = getattr(dialog, "network_probe_interval_spin", None)
    if spin is None:
        return _DEFAULT_INTERVAL
    try:
        return net_monitor.clamp_interval(spin.value())
    except Exception:  # pragma: no cover - 控件被销毁等
        return _DEFAULT_INTERVAL


def build_island_network_rows(dialog) -> list[SettingRow]:
    """岛侧行：四个指标开关（各行 objectName 前缀 settingRow_island_*）+ 间隔行。

    每个指标一行：SettingRow 只有一个 label/一个 buddy 控件，四个开关挤一行会让
    后几个开关拿不到可见的标签与可访问名（label.setBuddy 一位一控），故拆成
    四行、label 统一带「灵动岛指标」前缀（设置搜索按 label 命中）。

    行随域导航每次重建（行壳随旧页 deleteLater，控件对象复用），与 island /
    file_interpret 行的重建口径一致。**没有控件的 id 不建行**：清单可能在窗口打开
    之后变动，那一行的开关并不存在（装配一个 ``None`` buddy 只会做出一个空行）。

    **每行都建在宿主上（``parent=dialog``）**：行壳建出来时还没进卡片，而
    :func:`_install_visibility` 当场就要按依赖位 ``setVisible``——无父控件的
    ``setVisible(True)`` 会当场开出一个顶层窗口（Windows 上就是用户看到的无标题
    小窗，见 ``pet/chat/settings_dialog.py`` 同款坑的注释）。
    """
    create_network_controls(dialog)
    rows = [
        SettingRow(key, title, hint, getattr(dialog, _metric_attr(metric_id), None),
                   parent=dialog)
        for metric_id, key, title, hint in metric_rows()
        if getattr(dialog, _metric_attr(metric_id), None) is not None
    ]
    rows.append(SettingRow(
        "network_probe_interval",
        "延迟探测间隔",
        _interval_hint(dialog),
        dialog.network_probe_interval_spin,
        parent=dialog,
    ))
    rows.extend(build_game_fps_rows(dialog))
    _install_visibility(dialog)
    return rows


def build_game_fps_rows(dialog) -> list[SettingRow]:
    """游戏帧率的三行：指标开关（在上面的 ``_METRIC_ROWS`` 里）+ 目标模式 + 程序名。

    **新行一律追加在末尾**（同 ``_METRIC_ROWS`` 的取舍）：把新行插进中间会让老用户
    保存一次就把既有行序重排一次。前两行是"跟随设置"——只在帧率指标开启时才显示
    （与「延迟探测间隔」跟网络开关同规：关闭时留着可编辑就是"改了没反应"）。
    三行都建在宿主上（``parent=dialog``），理由同 :func:`build_island_network_rows`。
    """
    create_network_controls(dialog)
    create_fps_controls(dialog)
    return [
        SettingRow(
            "game_fps_target_mode",
            "帧率采集目标",
            "跟随前台程序：采集当前在前台运行的游戏（切到别的程序时按新目标重新采集，"
            "上一个目标的旧读数会被丢掉，不会留在岛上）。指定程序：只采集下面填的"
            "那个程序——窗口化游戏的前台窗口不一定是游戏本体时用它。",
            dialog.game_fps_target_mode_select,
            parent=dialog,
        ),
        SettingRow(
            "game_fps_process",
            "指定的程序名",
            "填可执行文件名，例如 game.exe：只按名字匹配进程，不会启动、也不会执行"
            "这个程序（填整条路径也只取文件名）。名字里带空格照填，不用加引号"
            "（例如 My Game.exe）。只在「指定程序」模式下可编辑；"
            "留空等于没有目标——不会替你挑一个帧率最高的程序来显示。",
            dialog.game_fps_process_edit,
            parent=dialog,
        ),
    ] + _fps_permission_rows(dialog)


def _fps_permission_rows(dialog) -> list[SettingRow]:
    """授权行（**仅 Windows**：ETW 权限是 Windows 专属，别处不创建控件）。

    这一行没有配置键、不写盘：权限状态每次打开设置页用进程内令牌 / 组成员 API 现查，
    点按钮才弹系统 UAC 把当前用户加进 ``Performance Log Users`` 组。
    本行同样在 ``_FPS_FOLLOW_KEYS`` 里跟着帧率开关显隐，故也建在宿主上。
    """
    button = getattr(dialog, "game_fps_permission_btn", None)
    if button is None:
        return []
    return [
        SettingRow(
            "game_fps_permission",
            "帧率采集权限",
            "Windows 只允许管理员或「Performance Log Users」组成员读取游戏的呈现事件。"
            "点按钮会弹出一次系统的管理员授权窗口，把当前账户加入该组——桌宠本身仍以普通"
            "权限运行，不装服务、不常驻提权进程。加入后，注销或重启电脑才生效；"
            "要撤销，在「计算机管理 · 本地用户和组」里把自己移出该组即可。",
            button,
            parent=dialog,
        )
    ]


def _create_fps_permission_button(dialog) -> None:
    if not fps_permission.is_supported():
        return
    if getattr(dialog, "game_fps_permission_btn", None) is not None:
        return
    button = QPushButton(_FPS_PERMISSION_BUTTON, dialog)
    button.setObjectName("gameFpsPermissionButton")
    button.clicked.connect(lambda: start_fps_permission_grant(dialog))
    dialog.game_fps_permission_btn = button
    # query_status 走进程内令牌 / 组成员 API，不起子进程，不卡设置页。设置页默认是独立
    # 进程：关掉重开后「已加入待重新登录」靠账户实际组成员关系判出，不靠本进程内存
    if _FPS_GRANT_JOB.get("thread") is not None:
        # 同进程内重建对话框：任务在跑就接上轮询；已结束就直接回填它的结果
        _attach_fps_permission_poll(dialog)
        button.setEnabled(False)
        button.setText("等待系统授权…")
        poll_fps_permission_grant(dialog)
    else:
        _apply_fps_permission_status(dialog, fps_permission.query_status())


def _apply_fps_permission_status(dialog, status: str) -> None:
    button = dialog.game_fps_permission_btn
    button.setText(_FPS_PERMISSION_TEXT.get(status, _FPS_PERMISSION_BUTTON))
    # 只有「未授权 / 判不出」时可点：已生效或待重新登录时再点一次只会重复弹 UAC
    button.setEnabled(status in (fps_permission.STATUS_MISSING,
                                 fps_permission.STATUS_UNSUPPORTED))


def start_fps_permission_grant(dialog, *, grant=None) -> None:
    """按钮入口：后台线程跑 UAC 授权（会阻塞到用户点完 UAC），定时器回收结果。

    授权任务是**进程级**的（``_FPS_GRANT_JOB``）：同一设置进程内关闭/重建对话框不影响它，
    同一时刻只跑一个。设置进程退出时等待线程随之结束，但已拉起的 UAC/管理员进程不受影响；
    重开设置页以账户实际组成员关系为准（加组幂等，重复授权无副作用）。
    """
    grant = grant or fps_permission.grant
    button = dialog.game_fps_permission_btn
    button.setEnabled(False)
    button.setText("等待系统授权…")
    running = _FPS_GRANT_JOB.get("thread")
    if running is None or not running.is_alive():
        job: dict = {}

        def worker() -> None:
            try:
                job["result"] = grant()
            except Exception as exc:  # noqa: BLE001 — 结果必须回到 GUI，不能静默吞掉
                job["result"] = fps_permission.GrantResult("error", f"授权异常：{exc}")

        thread = threading.Thread(target=worker, name="fps-permission-grant", daemon=True)
        _FPS_GRANT_JOB.clear()
        _FPS_GRANT_JOB.update(thread=thread, job=job)
        thread.start()
    _attach_fps_permission_poll(dialog)


def _attach_fps_permission_poll(dialog) -> None:
    # 定时器挂在对话框上：对话框销毁即随之回收，不会回调已删除的控件
    timer = getattr(dialog, "_fps_permission_timer", None)
    if timer is None:
        timer = QTimer(dialog)
        timer.setInterval(_FPS_PERMISSION_POLL_MS)
        timer.timeout.connect(lambda: poll_fps_permission_grant(dialog))
        dialog._fps_permission_timer = timer
    timer.start()


def poll_fps_permission_grant(dialog) -> None:
    result = _FPS_GRANT_JOB.get("job", {}).get("result")
    if result is None:
        return
    dialog._fps_permission_timer.stop()
    _apply_fps_permission_status(dialog, fps_permission.query_status())
    button = dialog.game_fps_permission_btn
    button.setToolTip("" if result.outcome == "ok" else result.detail)


def build_menu_rows(dialog) -> list[SettingRow]:
    """菜单侧一行：右键菜单「网络信息」入口显隐。"""
    create_network_controls(dialog)
    return [
        SettingRow(
            "network_info_menu",
            "「网络信息」菜单入口",
            "右键菜单里显示「网络信息」：公网 IP、IP 属地（及是否在中国大陆）、"
            "Google / GitHub 可达性。只在打开弹窗时取一次（约 1 KB），不轮询；"
            "关掉后菜单里保留该入口并置灰，悬停能看到关闭原因。",
            dialog.network_info_menu_check,
        ),
    ]


def create_fps_controls(dialog) -> None:
    """建游戏帧率的目标控件（幂等）：模式下拉 + 程序名输入。

    两个控件都是"进程级"键的编辑面（``game_fps_target_mode`` / ``game_fps_process``
    不在 ``PER_SLOT_SETTING_KEYS`` 里，岛与采集链路一律读主配置）：子宠页保存由设置
    进程的配置路由直写主配置（``pet/settings_slot_config.py``），所以这里不置灰。

    初值走 config 的**同一份归一化**（``_clean_game_fps_target_mode`` /
    ``_clean_game_fps_process``）：脏配置打开设置页时看到的是运行期真正生效的值，
    而不是磁盘上那份没被收敛的原文（两者不一致才是真的"改了没反应"）。
    """
    if getattr(dialog, "game_fps_target_mode_select", None) is None:
        # 宽度按字体度量走（见 _FpsModeSelect）：放大字体时下拉不裁字，窄窗口换行
        select = _FpsModeSelect(dialog)
        for value, text in _FPS_TARGET_MODES:
            select.addItem(text, value)
        select.setCurrentData(_config_target_mode(dialog))
        dialog.game_fps_target_mode_select = select

    if getattr(dialog, "game_fps_process_edit", None) is None:
        edit = QLineEdit(dialog)
        # 输入上限与配置归一化的上限同源：这里先卡住，用户在输入框里就撞不到静默截断
        edit.setMaxLength(GAME_FPS_PROCESS_MAX_CHARS)
        edit.setPlaceholderText("例如 game.exe")
        edit.setText(_config_process_name(dialog))
        _fit_process_edit(edit)
        dialog.game_fps_process_edit = edit

    _create_fps_permission_button(dialog)


def _fit_process_edit(edit) -> None:
    """按字体度量给程序名输入框定最小宽度（与间隔 spin 的 ``_fit_spin_to_content`` 同法）。

    默认宽度按"十几个字符"给，长 exe 名（放大字体下更早）会被裁成看不全；这里按样本
    实测宽度加余量，并解除任何固定宽度——窄窗口放不下时由 ``SettingRow`` 的响应式
    换行接管（它按字体度量算行高）。
    """
    needed = edit.fontMetrics().horizontalAdvance(_FPS_PROCESS_SAMPLE) + 24
    edit.setMinimumWidth(max(needed, 200))
    edit.setMaximumWidth(_QWIDGETSIZE_MAX)


def _config_target_mode(dialog) -> str:
    return _clean_game_fps_target_mode(dialog.config.get("game_fps_target_mode"))


def _config_process_name(dialog) -> str:
    return _clean_game_fps_process(dialog.config.get("game_fps_process"))


def _sync_fps_target(dialog) -> None:
    """模式联动：只有「指定程序」模式下程序名输入才可编辑。

    置灰而不是隐藏：切回「跟随前台程序」时用户还要看得见自己填过的名字（值保留、
    只是不生效）——把它藏起来会让人以为名字被清掉了。
    """
    select = getattr(dialog, "game_fps_target_mode_select", None)
    edit = getattr(dialog, "game_fps_process_edit", None)
    if select is None or edit is None:
        return
    in_process_mode = str(select.currentData() or "") == _FPS_PROCESS_MODE
    edit.setEnabled(in_process_mode)
    edit.setToolTip(_FPS_PROCESS_TOOLTIP if in_process_mode else _FPS_PROCESS_DISABLED_TOOLTIP)


def _install_visibility(dialog) -> None:
    """把岛侧三级的显隐依赖接上（岛开关 → 信息槽开关 → 指标行 / 间隔行）。

    依赖位（``_visibility_dependencies``）与 ``settings_pet_controls`` 写的那些位
    按 AND 合成，两边互不干扰、与调用顺序无关。信号只连一次；行壳每次域导航重建
    都是新的，故信号回调统一读 ``dialog._island_network_sync`` 上的最新闭包。

    行壳建在宿主上（``parent=dialog``），所以这里的 ``findChild`` 一次就能命中，
    不必再对无父的行补 ``setVisible``——那条补丁正是「无父控件被显示成顶层窗口」
    的来源（见 :func:`build_island_network_rows` 的 docstring）。
    """

    def apply(key: str, visible: bool, dependency: str) -> None:
        dialog._set_setting_rows_visible((key,), visible, dependency=dependency)

    def sync(*_args) -> None:
        island_on = bool(dialog.island_enabled_check.isChecked())
        info_on = bool(dialog.island_info_check.isChecked())
        net_on = bool(dialog.island_network_check.isChecked())
        fps_on = bool(dialog.island_fps_check.isChecked())
        # 指标行同进同出（都是"信息槽上的附加段"，含第三方组件行）；间隔行只跟网络
        # 开关走——CPU / 内存段不产生探测包，没有间隔可言；游戏帧率的两行跟随设置
        # 只跟帧率开关走（同规：关闭时留着可编辑就是"改了没反应"）。
        for _metric_id, key, _title, _hint in metric_rows():
            apply(key, island_on and info_on, "island_info_slot_shown")
        apply("network_probe_interval", island_on and info_on and net_on,
              "island_network_on")
        for key in _FPS_FOLLOW_KEYS:
            apply(key, island_on and info_on and fps_on, "game_fps_on")
        # 模式 → 程序名可编辑性（置灰不是隐藏：切回前台模式时值仍要看得见）
        _sync_fps_target(dialog)
        # 「信息槽内容 / 自定义短文本」两行**不受**网络开关影响（P1 Slice D2）：
        # 网络段自 D1 起是普通指标段，不再接管信息槽，info_mode 始终生效——
        # 这里曾经把两行隐掉，那是"网络接管信息槽"旧语义的残留，会让用户改了
        # 内容选择却看不出效果（真正的"改了没反应"）。

    dialog._island_network_sync = sync
    if not getattr(dialog, "_island_network_visibility_wired", False):
        dialog._island_network_visibility_wired = True
        for checkbox in (dialog.island_enabled_check, dialog.island_info_check,
                         dialog.island_network_check, dialog.island_fps_check):
            checkbox.toggled.connect(lambda *_a, host=dialog: host._island_network_sync())
        dialog.game_fps_target_mode_select.currentIndexChanged.connect(
            lambda *_a, host=dialog: host._island_network_sync())
    sync()


def _save_game_fps_settings(dialog) -> None:
    """游戏帧率两键的写回（fps 指标本身在 ``island_metrics`` 列表里，走上面的通用逻辑）。

    两个值都过 config 的**同一份归一化**再写：用户在输入框里填了 ``C:\\Games\\a.exe``
    或带参数的一长串，落盘的是收敛后的纯 exe 名——磁盘上永远只有一种形态，设置页
    回显、岛与采集链路读到的也就只有这一种（不新增第二份解释）。
    """
    select = getattr(dialog, "game_fps_target_mode_select", None)
    if select is None:
        return
    dialog.config.set("game_fps_target_mode",
                      _clean_game_fps_target_mode(select.currentData()))
    edit = getattr(dialog, "game_fps_process_edit", None)
    dialog.config.set("game_fps_process",
                      _clean_game_fps_process(None if edit is None else edit.text()))


def save_network_settings(dialog) -> None:
    """_write_config 委托：把指标列表与另两键写回 config（保存时调用一次）。

    写回列表 = **保留配置里的既有顺序** + 新勾选的按典范序追尾：复选框只表达
    "在/不在"（顺序属于配置，列表序 = 用户优先级 = 尾部先折叠，见 config 的
    ``island_metrics`` 键注释）。无条件按典范序重写会把用户手改的顺序压回去，
    所以已有且仍勾选的按原序在前、取消勾选的自然淘汰、新勾选的按
    ``_METRIC_ORDER`` 追到末尾（"新行追加在末尾"的取舍见 ``_METRIC_ROWS``）——
    勾选先后依旧不影响顺序。旧键 ``island_network_enabled`` 不再写——它是归一化
    阶段的一次性迁移来源，在设置页再写一遍就会重新长出"第二个真值"。

    **只覆盖本次表单真正建过控件的 id**（:func:`_form_metric_ids`）：窗口打开之后
    清单可能变动（用户装 / 卸了组件），那些 id 在表单里没有控件——
    ① 取它的值会 ``getattr`` 抛 AttributeError，把整条保存链（网络 → 音乐 → 静音 →
    天气 → ``config.save()``）断在写入之前，这次保存一个键都不落盘；
    ② 把它当"用户没勾"淘汰，会静默删掉用户的偏好。所以"表单没覆盖的 id"一律
    按磁盘现状保留（用户没编辑过它，就不该被这次保存改写），顺序也留在原位。

    灵动岛模块未激活时（期 4c）：岛侧行（指标 5 段 + 第三方组件行、探测间隔、
    帧率两键）未挂出，这些键一律不写回——控件还在（重新激活原样回来），但
    "没显示的行不该被这次保存改写"。菜单域的 ``network_info_menu`` 行不受岛
    门控（它挂在「菜单 → 菜单编排」域），照常写回。
    """
    if getattr(dialog, "island_network_check", None) is None:
        return
    dialog.config.set("network_info_menu_enabled",
                      bool(dialog.network_info_menu_check.isChecked()))
    if not feature_activation.is_active("dynamic_island"):
        return
    covered = _form_metric_ids(dialog)
    checked = {metric_id for metric_id in covered
               if getattr(dialog, _metric_attr(metric_id)).isChecked()}
    kept = [metric_id for metric_id in _checked_metrics(dialog)
            if metric_id not in covered or metric_id in checked]
    dialog.config.set("island_metrics",
                      kept + [m for m in covered if m in checked and m not in kept])
    dialog.config.set("network_probe_interval_seconds", _spin_value(dialog))
    _save_game_fps_settings(dialog)
