# -*- coding: utf-8 -*-
"""独立灵动岛胶囊窗口。

- 常驻顶层小窗，可显示图标、名称、信息槽、状态灯；
- 信息槽是一行**段**（主信息段 + ``island_metrics`` 的指标段 + 折叠 ``+N`` chip）：
  窄屏时按收缩流水线逐级让位（省名字 → 名字隐藏 → 尾部整段折 +N → 省 custom
  主信息），被折段同时撤销采样；段数据/落位/收缩判定全在 ``island_layout.py``；
- 支持拖拽、四边停靠半隐藏（收成细条、鼠标靠近滑出）、位置持久化；
- 单击胶囊按 ``click_action`` 展开卡片（余额/最近消息/快捷按钮）或切换桌宠显隐；
  桌宠隐藏时（``hidden_chat`` 开启）单击改为弹岛对话气泡（island_chat.py）；
- 事件动效（``event_effects``）：AI 回复/余额刷新/峰谷切换时位移弹跳 +
  表面轻压 + 短暂呼吸，静止时完全无定时器开销；状态点是形状+颜色双通道五态
  （空闲中性 / 工作·待确认跟主题色 / 完成绿 4s 瞬态 / 错误红+感叹 / 隐藏·离线空心环，
  见 :func:`_status_error_glyph_path` 与 ``_status_dot_state``）；
- ``bump()`` 供碰撞系统调用：被撞时表面压扁回弹（史莱姆果冻形变只作用于
  胶囊外形，文字图标保持锐利）+ 按撞击方向轻微踢动；
- 外观：黑/白/玻璃质感自绘 + 背景不透明度 + 主题色（五级）。
"""
from __future__ import annotations

import logging
import math
import sys
import time
from dataclasses import dataclass

from PySide6.QtCore import (
    QEvent, QPoint, QPointF, QRect, QRectF, QSize, Qt, QTimer, Signal,
)
from PySide6.QtGui import (
    QColor, QCursor, QFont, QFontMetrics, QGuiApplication, QLinearGradient, QPainter,
    QPainterPath, QPen, QPixmap,
)
from PySide6.QtWidgets import (
    QApplication, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget,
)

from . import (
    catalog, gpu_monitor, island_extension_host, island_fps, island_layout,
    island_segments, net_monitor, quiet_mode, sys_monitor,
)
from .config import _clean_island_coordinate
from .dsh_state import DshState
from .island_layout import (
    _BASELINE_DY, _CHIP_ALERT_DOT_PX, _CHIP_H_PX, _CHIP_RADIUS_PX, _CONTENT_PAD_PX,
    _DEVICE_LABELS, _DIVIDER_H_PX, _DIVIDER_W_PX, _GPU_LABEL, _ICON_GAP_PX, _ICON_PX,
    _NAME_GAP_PX, _NAME_MAX_PX, _NETWORK_BOX_SAMPLE, _PRIMARY_ELIDE_PX,
    _PRIMARY_SAMPLES, _STATUS_DOT_PX, _STATUS_GAP_PX, _SYS_METRIC_IDS, _InfoSeg,
    place_info_segments,
)

logger = logging.getLogger(__name__)

_CAPSULE_HEIGHT = 44
_CAPSULE_INSET = 6  # 胶囊绘制内缩：给果冻形变/悬停放大/踢动留出窗口内余量
_EDGE_MARGIN = 16
_DOCK_THRESHOLD = 40
_STRIP_THICKNESS = 16
_STRIP_SIDE = 64  # 左右停靠时竖条的长度
#: 胶囊投影三层：(两侧内收 px, 底边下伸 px, 纯黑 alpha)。
#: 单层"整块下移 2px"既做不出近实远虚的落影，底缘也只剩一条硬边。三层一律
#: **两侧内收、只向下长**——窗口高只有 44（胶囊 y=6..38），左右余量已被
#: scale 1.03 / squish 鼓出吃掉，向外扩会被窗口硬切。最低层底边 38+4=42，
#: 放大到 1.03 后 ≈42.6 < 44，安全。
_CAPSULE_SHADOW_LAYERS = ((1.0, 1.0, 28), (2.0, 2.5, 16), (3.0, 4.0, 8))
#: 胶囊顶部光泽（sheen）：受光的一道淡白带，画在内容之下、外壳之上。
#: 起点从 ``rect.top()+_SHEEN_TOP_PX`` 起——上面两行已被外描边（keyline）与软陶笔
#: 占住（那两行是既有像素契约的采样行，光泽不得改它们的值）。亮风跳过：白底上
#: 白光泽等于没有，只会把顶部那两道白笔糊宽。
_SHEEN_TOP_PX = 3.0      # 从 rect.top()+3 起，避开 keyline 行和 bevel 取样行
_SHEEN_SPAN = 0.42
#: 按 style 分档的峰值 alpha（不乘 _opacity：外壳质感不随内容透明度走，同 keyline）
_SHEEN_ALPHA = {"dark": 14, "light": 0, "glass": 34}
_CARD_WIDTH = 340      # 卡片宽下限（沿用现状视觉记忆）
_CARD_WIDTH_MAX = 420  # 卡片宽上限（k3 §5.1：防长来源说明把卡片拉成横幅）
_CARD_AUTO_COLLAPSE_MS = 15_000
_CARD_TICK_MS = 1000   # 卡片开着时的每秒 tick：刷新"更新时间"，收起即停表
_DOCK_BACK_MS = 800
_BREATHE_AFTER_EVENT_S = 5.0

# 弹簧参数：位移踢动（kick）、受击倾斜（tilt）、果冻形变（squish）
_KICK_STIFFNESS = 210.0
_KICK_DAMPING = 17.0
_SCALE_STIFFNESS = 170.0
_SCALE_DAMPING = 15.0
_TILT_STIFFNESS = 120.0
_TILT_DAMPING = 10.0
_TILT_MAX_DEG = 3.5
_SCALE_MIN = 0.97
_SCALE_MAX = 1.03
# 史莱姆形变：只作用于胶囊外形（背景/描边/阴影），文字图标绝不参与变换——
# 形变的是"表面"，内容保持锐利（1.0=休息态，<1 压扁，弹簧回摆产生果冻抖动）
_SQUISH_STIFFNESS = 150.0
_SQUISH_DAMPING = 8.5
_SQUISH_MIN = 0.85
_SQUISH_MAX = 1.12
# 受击速度上限：单次最强撞击（bump 的 strength 封顶 3.0）注入 -3.45 / ±39，
# 就是这两个上界的来源。连撞叠加不得越过它——越过只是把弹簧顶在形变钳位
# （_SQUISH_MIN）与 ±_TILT_MAX_DEG 倾斜钳位上反复过冲：形状不再变化（像素零
# 变化），_animating() 却长期为真，半透明顶层窗每 16ms 空转整窗重绘（实测连撞
# 60 次 _squish_v 累到 -207、同向连打 _tilt_v 累到 109，均无界）。
_SQUISH_V_MAX = 3.5
_TILT_V_MAX = 40.0
# bump 动效的动画时间上限（s）：从最后一次撞击起算，到点强制收尾归零。连撞把
# 弹簧顶在钳位上时"阈值判据"可能长期为真，上限是收敛的硬保证（按动画时间累计，
# 不是墙钟——停表期间的墙钟不计入）。
_BUMP_ANIM_MAX_S = 0.9
_ANIM_TICK_MS = 16  # ~60fps，仅动画活跃时运行，静止零开销
#: 游戏帧率段的读表节拍（1Hz）：采集器自己按 0.25s 发布，GUI 只按秒读一次快照
#: （读数比这更勤地刷没有意义，而采样是后台线程的事——读表绝不进 16ms 动画预算）。
_FPS_TICK_MS = 1000

_DOCK_EDGES = ("none", "top", "bottom", "left", "right")

# 主题色预设（设置页可换）：图标底圈 / 事件闪光 / 停靠描边共用
_ACCENT_PRESETS = {
    "blue": "#40b8ff",
    "green": "#34c77b",
    "purple": "#9b7bff",
    "pink": "#ff7ab8",
    "orange": "#ffa94d",
}
_DEFAULT_ACCENT = "blue"

# 图标模式："auto"=鱼本体头像位图（默认）；"img:<路径>"=用户自定义图片；
# 其他字符串=文字/emoji 图标（用户主动选择，愿意付 emoji 字体栈的一次性税）。
_ICON_AUTO = "auto"
_ICON_IMG_PREFIX = "img:"
_ICON_PIXMAP_PX = 22  # 头像画进 26px 底圈内的尺寸
_ICON_STRIP_PX = 16  # 左右停靠细条里的头像尺寸

# ---- 状态点五态体系（P2 Slice E1，设计稿 §五定案）----
# 形状+颜色**双通道**，同刻只画一个图元；优先级 错误 > 待确认 > 工作 > 空闲/隐藏，
# 其中「完成」是空闲点位的 4s 替身（见 ``_status_dot_state``）。旧语义
# 「8px 实心绿 = 宠物可见」废弃：绿留给「完成」，中性色（主题次色）表示空闲。
_DOT_SOLID = "solid"    # 实心圆：空闲 / 工作 / 完成
_DOT_HOLLOW = "hollow"  # 空心环：待确认 / 宠物隐藏 / DSH 离线
_DOT_GLYPH = "glyph"    # 红实心 + 感叹矢量：错误
_DOT_IDLE = "idle"
_DOT_WORKING = "working"
_DOT_DONE = "done"
_DOT_WAITING = "waiting"
_DOT_ERROR = "error"
_DOT_HIDDEN = "hidden"
_DOT_RING_PX = 1.5      # 空心环描边宽度（外缘仍占满 8px 足迹，尺寸不变）
_DOT_DONE_MS = 4000     # 完成瞬态停留时长：4s 后回落，单次定时器随即销毁
_DOT_DONE_COLOR = "#50dc78"    # 完成绿（沿用旧「可见绿灯」色值，语义改为完成）
_DOT_ERROR_COLOR = "#f2777a"   # 错误红（与告警色体系的 crit 同色）
_DOT_GLYPH_COLOR = "#ffffff"   # 感叹笔画色（红底上两种主题都清晰）
_DOT_GLYPH_W_RATIO = 0.17      # 感叹笔画宽 / 点径（8px 点 → 1.36px）
#: 珠感高光：实心/感叹点画完后叠一道上半椭圆高光（空心环不画——环内本来是空的，
#: 高光会把环画回实心）。左内收 1.5px 与点径同款留边，高 0.38×点径。
_DOT_HIGHLIGHT_INSET_X = 1.5
_DOT_HIGHLIGHT_TOP = 1.0
_DOT_HIGHLIGHT_H_RATIO = 0.38
_DOT_HIGHLIGHT_ALPHA = 70
#: 头像底圈的内侧细光圈（把底圈从"一块色斑"勾成"一枚圆章"）：暗/玻璃底白、
#: 亮底黑（白圈在白底上不可见）。色随 style 分档，所以 style 必须进内容缓存键。
_ICON_RING_DARK = (255, 255, 255, 46)
_ICON_RING_LIGHT = (0, 0, 0, 20)

# ---- 工作态声纳环（P3 视觉打磨）----
# "在用"这件事从静态色点升级成有节拍的信号：状态点外扩一圈 accent 环，半径
# 线性扩散、alpha 按 (1-t)³ 收敛（= 1 - ease_out_cubic(t)，先亮后急收）。**只在
# 工作态播**——待确认是"等你"，完成是"刚做完"，错误要的是注意力，都不该有个
# 持续的节拍。驱动是独立 30fps 单表，起表条件收敛在 ``_sonar_eligible``，离开
# 工作态/隐藏/展开卡片/收进细条一律停表销毁（同 _dot_done_timer 的停表纪律）。
_SONAR_PERIOD_MS = 1600
_SONAR_TICK_MS = 33        # ~30fps：环是慢速呼吸，不需要 60fps
_SONAR_R0_PX = 5.0         # 起点半径：贴在 8px 点（半径 4）外 1px，从点位外起
_SONAR_R1_PX = 13.0
_SONAR_ALPHA = 90
_SONAR_PEN_PX = 1.5

# ---- 段数值告警色体系（P2 Slice E2，裁决稿 §三 ops5.5）----
# 两级告警色只染**数值**（含 E4 单位盒跟随值色）：阈值与滞回在 island_segments
# （纯判定），这里只管"哪一级用什么色"。亮底主题按同一色相加深档而不是新增主题
# 键——沿用 _divider_color/_keyline_color 那套"按 style 分支给常量"的既有口径。
_ALERT_WARN_DARK = "#f0b44c"    # 规格值：暗底 warn（琥珀）
_ALERT_CRIT_DARK = "#f2777a"    # 规格值：暗底 crit（与错误状态点同色）
_ALERT_WARN_LIGHT = "#95702f"   # 亮底加深档：色相 38° 不变、亮度 158→98
_ALERT_CRIT_LIGHT = "#964a4c"   # 亮底加深档：色相 358° 不变、亮度 181→112

# ---- 胶囊软陶笔（P2 Slice E3，裁决稿 §四 k3 高光笔）----
# 顶部 1px 内高光 + 底部 1px 内暗边：画在胶囊外形的**内缘**（既有 keyline 里侧
# 再 1px），随 squish 形变——高光是"表面受光"不是轮廓线，跟外壳一起形变才有软陶
# 厚度感。三风格各一档常量，与 _keyline_color/_divider_color 同款"按 style 分支"
# 口径（不新增主题键、不乘 _opacity：keyline 也不乘，外壳质感不随内容透明度走）。
_BEVEL_INSET = 1.5  # 笔路径内缩：1px 笔落在 keyline 里侧那一整行上（见 _paint_bevel）
_BEVEL_HIGHLIGHT_DARK = (255, 255, 255, 26)   # 白 α26 ≈ 10%（k3 §7.1）
_BEVEL_SHADOW_DARK = (0, 0, 0, 46)            # 黑 α46 ≈ 18%
_BEVEL_HIGHLIGHT_LIGHT = (255, 255, 255, 230)  # 白底上的高光几乎不可见（k3 §7.2）
_BEVEL_SHADOW_LIGHT = (0, 0, 0, 20)           # 亮风的厚度由底部黑暗边承托
_BEVEL_HIGHLIGHT_GLASS = (255, 255, 255, 90)  # 玻璃已有强外描边，内高光减半防双线
_BEVEL_SHADOW_GLASS = (35, 50, 60, 26)        # #23323C α26：带蓝相的暗边

# ---- 结构变化淡化（P2 Slice E3；裁决稿 §一-6 + 主代理裁决 2026-10-07 单侧口径）----
# 只在**结构变化**（段增删 / 折叠展开 / 主信息模式切换 / 收缩级变化）时淡化：旧内容
# 位图按 (1-t) 渐出、新位图直绘就位（**不**按 t 渐入——两侧都混会让未变化的文字
# 中途被稀释两成，见 _paint_capsule）；采样值换字零动画（硬规则）。驱动表起表才建、
# 到点即销毁（停表纪律，同 _dot_done_timer）。
_CONTENT_FADE_MS = 130       # 时长（施工单窗口 120-150ms）
_CONTENT_FADE_TICK_MS = 16   # ~60fps，与 _ANIM_TICK_MS 同口径

# ---- 展开卡片四分区（P2 Slice E5；裁决稿 §一-10 + §四 k3 卡片四分区）----
# 分工硬规则：胶囊 = 一眼（定宽读数），卡片 = 看懂（单位全称/来源口径/更新时间只在
# 卡片）。四分区自上而下 = 概览（主读数 + 状态文字）/ 指标详情（每段一行）/ 最近消息
# / 操作。高度**全部按字体度量实测**（k3 §5.2，不再有固定的 148）；每一区自带横分隔线
# 与上下间距，所以"没有指标段"时整区连分隔线一起消失，不会留下一条悬空的线。
_CARD_MARGIN_X = 16       # 内容左右边距（沿用现状 _build_card 的阅读边距）
_CARD_MARGIN_TOP = 10
_CARD_MARGIN_BOTTOM = 12
_CARD_LINE_GAP = 3        # 同一区内相邻行的行距
_CARD_SECTION_GAP = 10    # 分区到横分隔线的距离（线上下各一档，k3 §5.1）
_CARD_SEP_PX = 1          # 横分隔线厚度（色 = 段间竖线色，见 _divider_color）
_CARD_COL_GAP = 8         # 指标详情三列之间的间距
_CARD_NAME_COL_MAX = 44   # 段名列宽上限（超宽右省略，值列左缘因此对齐）
_CARD_ROW_PAD_Y = 2       # 指标行的上下内边距（行高 = 实测行高 + 2×它）
_CARD_DETAIL_MIN_PX = 96   # 详情列的最小可用宽（值列取宽时给它留下的底线）
_CARD_MESSAGE_MAX_LINES = 2   # 最近消息最多两行（k3 §5.1，沿用现状限高）
_CARD_BUTTON_H = 28       # 操作行按钮高（k3 §5.1）
_CARD_STATUS_DOT_GAP = 6  # 概览区状态点与状态文字的间距
#: 详情列宽样本里的"更新时间"占位：宽度只吃这个固定串、不吃真实读数
#: （"3 秒前" → "00 秒前"），所以采样值永远改不动卡片宽（与胶囊宽度白名单同纪律）。
_CARD_UPDATED_SAMPLE = "00 秒前"
#: 卡片里每段的人话名字（胶囊里网络段是纯 glyph 无 label，卡片得说清是哪一段）。
_CARD_METRIC_NAMES = {
    island_segments.NETWORK: "网络",
    island_segments.CPU: "CPU",
    island_segments.MEM: "内存",
    island_segments.GPU: "GPU",
    island_segments.FPS: "FPS",
}
#: 卡片里的**单位全称**（k3 §5.3：胶囊只给紧凑读数，量纲在卡片说清）。
#: 刻意短：这一列和"来源 · 更新时间"共用一个详情列，写长了会把更新时间挤掉
#: （宽度的上限是 420，见 _CARD_WIDTH_MAX）。
_CARD_UNIT_FULL = {
    island_segments.NETWORK: "速率 KB/s · 延迟 ms",
    island_segments.CPU: "百分比",
    island_segments.MEM: "百分比",
}
#: 概览区的状态文字（E1 五态语义的字面化；颜色仍走 ``_status_dot_style``）。
#: ``_DOT_HIDDEN`` 不在这张表里——它要分"桌宠隐藏"与"DSH 离线"两种说法。
_CARD_STATUS_TEXT = {
    _DOT_IDLE: "空闲",
    _DOT_WORKING: "工作中",
    _DOT_DONE: "刚完成",
    _DOT_WAITING: "待确认",
    _DOT_ERROR: "出错",
}
#: 四态兜底/不可用段在详情列的整句说明（顶掉"单位 · 来源 · 时间"三段）。
_CARD_UNAVAILABLE_NOTE = "该指标不可用"
_CARD_UNKNOWN_REASON = "未注册的段 id"
#: 段的**来源口径**（k3 §5.3：卡片说清"这个数从哪来"，胶囊一个字都不提）。
#: 口径一律用人话：网络段的读网卡计数器 / 探 DNS 都是实现细节（设置页那一刀
#: 的去黑话同约定），用户要知道的只是"本机采出来的"。
_CARD_SOURCE_SYS = "本机采样"
_CARD_SOURCE_NET = "本机采样"
#: 卡片行完整上下文（tooltip / 无障碍描述）的字符上限。细节全是外部输入（显卡名、
#: 引擎名、PDH 错误原文），放它无限长只会把 tooltip 撑成一个窗口。取段契约 detail
#: 上限的两倍：它与详情列那一列放的**同一批**信息，只是不省略。
_CARD_HINT_MAX_CHARS = island_segments.MAX_DETAIL_CHARS * 2


def _card_hint_text(*parts) -> str:
    """把若干段上下文拼成一条单行、限长的 tooltip 串（空项自动省略）。"""
    glued = " · ".join(
        str(part).replace("\r", " ").replace("\n", " ").strip()
        for part in parts if part)
    return glued[:_CARD_HINT_MAX_CHARS]


def _status_error_glyph_path(rect: QRectF) -> QPainterPath:
    """错误感叹矢量：两笔（竖杠 + 圆点），按点位矩形内相对坐标生成。

    两笔都是同一支圆头画笔（``RoundCap``）描出来的路径子段：竖杠是一段直线，
    圆点是**近零长**直线——零长线段 Qt 不描圆头（探针实测无像素），故留
    0.02px 让 cap 落地。用矢量而不是字符 ``!``：免字体回退税，且 8px 点位与
    停靠细条里的同一图元逐像素一致。

    比例取 0.18/0.46/0.82：圆头帽各向外延伸半个笔宽（≈0.7px），杠尾与点之间
    必须剩下 ≥1px 的红分隔，否则 8px 点上两笔会糊成一根竖条。
    """
    cx = rect.center().x()
    top = rect.top() + rect.height() * 0.18
    bar_end = rect.top() + rect.height() * 0.46
    dot_y = rect.top() + rect.height() * 0.82
    path = QPainterPath()
    path.moveTo(cx, top)
    path.lineTo(cx, bar_end)
    path.moveTo(cx, dot_y)
    path.lineTo(cx, dot_y + 0.02)
    return path


def spring_step(
    value: float, velocity: float, target: float, dt: float,
    stiffness: float = _KICK_STIFFNESS, damping: float = _KICK_DAMPING,
) -> tuple[float, float]:
    """阻尼弹簧积分一步（半隐式欧拉），返回新的 (value, velocity)。

    dt 由调用方按真实帧间隔传入（钳到 50ms 防切后台后爆炸）。
    """
    dt = max(0.0, min(float(dt), 0.05))
    accel = (target - value) * stiffness - velocity * damping
    velocity += accel * dt
    value += velocity * dt
    return value, velocity


def ease_out_cubic(t: float) -> float:
    t = max(0.0, min(1.0, float(t)))
    return 1.0 - (1.0 - t) ** 3


def sonar_ring_params(t: float) -> tuple[float, int]:
    """工作态声纳环在归一化时刻 ``t``（0..1，一个周期内的相位）的 (半径 px, alpha)。

    半径线性扩散（``_SONAR_R0_PX`` → ``_SONAR_R1_PX``）；alpha 走 (1−t)³
    ——即 ``1 − ease_out_cubic(t)``：起手最亮，越扩越淡，末段几乎瞬间收干净，
    不留"最后一帧还看得见一圈"的尾巴。纯函数：绘制只消费它的返回值，曲线口径
    在测试里直接钉端点与单调性。
    """
    phase = max(0.0, min(1.0, float(t)))
    radius = _SONAR_R0_PX + (_SONAR_R1_PX - _SONAR_R0_PX) * phase
    alpha = round(_SONAR_ALPHA * (1.0 - ease_out_cubic(phase)))
    return radius, alpha


def dock_edge_for(
    rect: QRect, available: QRect, threshold: int = _DOCK_THRESHOLD,
) -> str:
    """按窗口矩形到屏幕可用区四边的距离判定停靠边，都不近则 "none"。

    四边等权、取最近者——顶部被系统栏/MyDockFinder 占用时用户可靠左右下。
    """
    distances = {
        "top": abs(rect.top() - available.top()),
        "bottom": abs(available.bottom() - rect.bottom()),
        "left": abs(rect.left() - available.left()),
        "right": abs(available.right() - rect.right()),
    }
    edge, distance = min(distances.items(), key=lambda item: item[1])
    return edge if distance <= threshold else "none"


def strip_rect_for(rect: QRect, edge: str, available: QRect) -> QRect:
    """停靠后的细条矩形：上下停靠压扁高度，左右停靠收成短竖条。

    左右竖条按参考矩形的真实几何中心（top + height//2）纵向居中，不用
    QRect::center()——它对偶数尺寸向下取整差 1px，与滑出方向（_target_rect
    的整数居中公式）不对称，滑出/收回每循环细条累计上漂 1px。
    """
    if edge in ("top", "bottom"):
        y = available.top() if edge == "top" else available.bottom() - _STRIP_THICKNESS + 1
        return QRect(rect.left(), y, rect.width(), _STRIP_THICKNESS)
    if edge in ("left", "right"):
        x = available.left() if edge == "left" else available.right() - _STRIP_THICKNESS + 1
        length = min(_STRIP_SIDE, available.height())
        center_y = rect.top() + rect.height() // 2
        y = max(available.top(), min(center_y - length // 2,
                                     available.bottom() - length + 1))
        return QRect(x, y, _STRIP_THICKNESS, length)
    return QRect(rect)


def _cfg_dict(config) -> dict:
    value = config.get("dynamic_island", {})
    return value if isinstance(value, dict) else {}


def _native_reassert_topmost(window) -> bool:
    """Windows：把窗口原生重插到 topmost 带最上（不激活/不移动/不改大小）。

    非 Windows 一律返回 ``False``。macOS 的 NSWindow level 与 Windows 的
    topmost 带不是一回事，不能把 Qt ``raise_()`` 当成安全回退。
    """
    if sys.platform != "win32":
        return False
    try:
        from .platform_win import _set_windows_topmost

        return bool(_set_windows_topmost(int(window.winId()), True))
    except Exception:
        logger.debug("灵动岛原生置顶重申失败", exc_info=True)
        return False


@dataclass(frozen=True)
class _CardMetricRow:
    """卡片 B 区一行（指标详情）——**数据不是绘制串**，三列文案一次算清。

    三列 = 段名 / 当前值**合并串** / 详情（单位全称 · 来源口径 · 更新时间）。
    ``value_sample`` 与 ``detail_sample`` 是**宽度账目**用的声明样本（值列吃段自己
    声明的 ``box_text``，详情列把更新时间换成固定样本）——卡片宽由它们算出，
    **真实读数一个都不进**：采样值因此永远改不动卡片几何（与胶囊的宽度白名单
    同一条纪律）。``note`` 非空时顶掉详情三段（四态兜底/不可用段写整句原因）。
    """

    seg_id: str
    name: str
    value: str
    value_sample: str = ""
    unit_full: str = ""
    source: str = ""
    updated: str = ""
    state: str = island_segments.STATE_OK
    severity: str = ""
    note: str = ""
    #: 完整上下文（tooltip / 无障碍描述读它）：详情列只有一列宽，长显卡名与 PDH
    #: 来源口径必然被右省略——省略是排版，不是"这条信息不存在"。它**不进任何宽度
    #: 样本**（``detail_sample`` 只认 unit_full/source/固定时间样本），所以多长的
    #: 上下文都改不动卡片几何。
    hint: str = ""

    @property
    def detail(self) -> str:
        """详情列现文案：单位全称 · 来源 · 更新时间（空项自动省略）。"""
        if self.note:
            return self.note
        return " · ".join(
            part for part in (self.unit_full, self.source, self.updated) if part)

    @property
    def detail_short(self) -> str:
        """详情列的**降级文案**：放不下时先省掉单位全称，保住来源与更新时间。

        降级顺序是刻意的：单位缩写在值列里已经看得见（``38%``/``↓ 2.4M``），而
        "这个数从哪来、什么时候采的"是卡片独有、胶囊上一个字都没有的信息——空间
        不够时先舍重复表述，再走右省略兜底。
        """
        if self.note:
            return self.note
        return " · ".join(part for part in (self.source, self.updated) if part)

    @property
    def detail_sample(self) -> str:
        """详情列宽样本：更新时间换固定样本，读数变字不改宽。"""
        if self.note:
            return self.note
        return " · ".join(
            part for part in (self.unit_full, self.source, _CARD_UPDATED_SAMPLE)
            if part)


class _CardSeparator(QWidget):
    """卡片横分隔线（1px）：色 = 段间竖线色，左右由容器边距管着不出头。

    自绘而不是 QFrame/样式表：颜色要跟着 ``_divider_color`` 的三风格分支走，
    自绘是唯一不依赖"样式表是否恰好命中这个控件"的画法（k3 §7 的横分隔线就是
    竖分隔线同色）。
    """

    def __init__(self, island: "DynamicIsland", parent: QWidget | None = None):
        super().__init__(parent)
        self._island = island
        self.setFixedHeight(_CARD_SEP_PX)

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt 命名)
        painter = QPainter(self)
        painter.fillRect(self.rect(), self._island._divider_color())
        painter.end()


class _CardStatusDot(QWidget):
    """概览区的 8px 状态点：**直接复用**胶囊的 ``_paint_status_dot``。

    形状+颜色双通道是 E1 的语汇（实心 = 空闲/工作/完成，空心环 = 待确认/隐藏/
    离线，红实心+感叹 = 错误）。卡片里跟胶囊画同一枚图元，两处不可能漂移成
    两套样子——重画一遍路径才是漂移的开始。
    """

    def __init__(self, island: "DynamicIsland", parent: QWidget | None = None):
        super().__init__(parent)
        self._island = island
        self.setFixedSize(int(_STATUS_DOT_PX), int(_STATUS_DOT_PX))

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt 命名)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        self._island._paint_status_dot(
            painter, QRectF(0, 0, _STATUS_DOT_PX, _STATUS_DOT_PX))
        painter.end()


# ---------------------------------------------------------------- 段管线（P1 D1/D2）
# 胶囊内容行 = 头像 + 角色名 + 主信息段 + 指标段… + 状态点（见设计稿 §1.1）。
# 行内几何、段数据与收缩判定全部下沉在 ``pet/island_layout.py``（零 Qt 纯函数层，
# 度量对象注入、替身上可验）；本文件只管"什么时候重算、拿什么值来算、画到
# QPainter 上"——所以这里只剩字体与绘制相关的常量。
#: 字号阶梯（逻辑 px；``setPixelSize`` 不用 pt——pt 要过一次 DPI 换算的二次取整）。
#: L1 主信息 / L2 指标数值 / L3 角色名（卡片里当段名列）/ L4 段标签·单位 /
#: L5 卡片辅助文字（单位全称·来源·更新时间·最近消息·状态文字，k3 §5.1 的 11px 档）。
_FONT_LAYERS = {
    "primary": (14, QFont.Weight.DemiBold),
    "value": (13, QFont.Weight.DemiBold),
    "name": (12, QFont.Weight.Normal),
    "label": (10, QFont.Weight.Normal),
    "aux": (11, QFont.Weight.Normal),
}
#: 单位盒的降色系数（裁决稿 §二-5：单位弱化靠"10px + α×70%"双管，缺一档就是
#: 13px 与 10px 之间的"差 1px、弱化不足"）。
_UNIT_ALPHA = 0.7
#: 设备槽段的**数值盒**样本：``sys_monitor.MAX_TEXT`` 经同一条「值/单位分离」路径
#: 拆掉单位的那一半（单位本身取 ``island_segments.PERCENT_UNIT`` 声明值，单位盒宽
#: 由布局层另算，见 ``island_layout.place_info_segments``）。
_DEVICE_VALUE_SAMPLE = island_segments.split_value_unit(
    sys_monitor.MAX_TEXT, island_segments.PERCENT_UNIT)[0]
#: GPU 段的数值盒样本：同一条路径，样本来自 GPU 自己的最坏读数声明
#: （``gpu_monitor.MAX_TEXT`` = ``"100%"``，口径夹紧在 0–100）。
_GPU_VALUE_SAMPLE = island_segments.split_value_unit(
    gpu_monitor.MAX_TEXT, island_segments.PERCENT_UNIT)[0]


class DynamicIsland(QWidget):
    """胶囊形态的独立灵动岛窗口。"""

    clicked = Signal()  # click_action == "toggle_pet" 时的单击（旧行为）
    toggle_pet_requested = Signal()
    open_chat_requested = Signal()
    open_settings_requested = Signal()
    card_expanded = Signal()  # 卡片展开（AppShell 借此静默刷新余额）
    chat_requested = Signal()  # 桌宠隐藏时单击胶囊（hidden_chat 开启）：弹岛对话气泡

    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.config = config
        self._cfg = _cfg_dict(config)
        self.setObjectName("dynamic-island")
        flags = (
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        if hasattr(Qt.WidgetAttribute, "WA_MacAlwaysShowToolWindow"):
            self.setAttribute(Qt.WidgetAttribute.WA_MacAlwaysShowToolWindow, True)

        self._drag_offset: QPoint | None = None
        self._dragging = False
        self._press_global: QPoint | None = None
        self._balance_tier_text = "余额峰谷 --"
        self._balance_text = "余额 --"
        self._pet_visible = True
        # 本构建是否具备聊天能力（AppShell 按 enable_chat 喂入，默认 True
        # 维持现状；纯桌宠版置 False 时 hidden_chat 单击路由回退展开卡片）
        self._chat_available = True
        self._agent_active = False
        # 状态点五态体系的第三/第四输入（详见 _status_dot_state）：
        # _dsh_state = DSH 八态收敛态（"" = 该通道从未被喂过，纯桌宠构建走旧粗粒度位）；
        # _dot_done/_dot_done_timer = 完成瞬态的位与单次表（到点即停表销毁）
        self._dsh_state = ""
        self._dot_done = False
        self._dot_done_timer: QTimer | None = None
        # 工作态声纳环：_status_dot_rect = 内容层画点时顺手记下的点位矩形
        # （绘制层不自己算几何，见 _paint_sonar_ring）；_sonar_timer/_sonar_started
        # = 30fps 单表与本周期的起始时刻（相位按真实时间算，tick 抖动不吃曲线）。
        self._status_dot_rect: QRectF | None = None
        self._sonar_timer: QTimer | None = None
        self._sonar_started = 0.0
        self._last_message = ""
        # 几何变化回调（果冻墙碰撞体挂这里跟踪拖拽/停靠滑动）：AppShell 注入。
        self.on_geometry_changed = None
        # 桌宠聚合可见性回调（碰撞体据此挂起/恢复）：AppShell 注入。
        self.on_pet_visibility_changed = None

        # ---- 模式与动效状态（静止时全部为稳态，_anim_timer 不运行）----
        self._mode = "normal"  # normal / docked / expanded
        self._hover_peek = False  # 停靠状态下鼠标靠近的临时滑出
        # 位移弹跳（px）：事件下压回弹 / 受击定向踢动，绘制时整数化保持文字锐利
        self._kick_x = 0.0
        self._kick_y = 0.0
        self._kick_vx = 0.0
        self._kick_vy = 0.0
        self._tilt = 0.0
        self._tilt_v = 0.0
        # 果冻形变（史莱姆表面浮动）：只调制胶囊外形，不碰文字
        self._squish = 1.0
        self._squish_v = 0.0
        self._scale = 1.0  # 只做 ±3% 微缩放辅助（大缩放会糊文字，已弃用）
        self._scale_v = 0.0
        self._breathe_until = 0.0
        self._hover_scale_target = 1.0
        # bump 动效剩余动画时间（s）：每次撞击重置（见 _BUMP_ANIM_MAX_S）
        self._bump_anim_left = 0.0
        # 图标位图状态（icon="auto"/"img:<路径>" 时用；emoji 模式不碰）
        self._icon_provider = None  # 头像取图回调，AppShell 注入
        self._identity_provider = None  # 角色身份取源，AppShell 注入（见 set_identity_provider）
        self._icon_pixmap_cache: QPixmap | None = None
        self._icon_img_failed = False  # img: 加载失败也记住，免每帧重试磁盘 IO
        self._icon_pixmap_token = 0  # 头像换代计数：内容层缓存按它判失效
        # 内容层位图缓存（paintEvent 的文字绘制是实测大头，见 _content_cache）
        self._content_pixmap: QPixmap | None = None
        self._content_cache_key: tuple | None = None
        # 结构变化淡化（P2 Slice E3）：_content_drawn = 上一帧画出去的内容位图
        # （下一次结构变化的"旧位图"），_content_fade_from = 正在渐出的那一张，
        # _content_fade_t = 混合系数（1.0 = 无淡化，只画新位图），
        # _fade_struct_key = 上一帧的结构签名（判据见 _structure_key）。
        self._content_drawn: QPixmap | None = None
        self._content_fade_from: QPixmap | None = None
        self._content_fade_t = 1.0
        self._content_fade_timer: QTimer | None = None
        self._content_fade_last = 0.0
        self._fade_struct_key: tuple | None = None
        # 本帧画出去的结构签名（_content_cache 记录，_sync_content_fade 比对）
        self._painted_structure: tuple | None = None
        # 段管线的字体/行计划缓存：字号层懒建（_FONT_LAYERS），度量对象按层缓存；
        # 行计划（含收缩级）按白名单键缓存（见 _row_plan / _plan_inputs）——采样值
        # 变化进不了这个键，所以"数字跳动不推邻居"是结构保证而不是纪律。
        self._fonts: dict[str, QFont] = {}
        self._font_metrics: dict[str, QFontMetrics] = {}
        self._plan_inputs_key: tuple | None = None
        # 最近一次**问过**的输入键（`_row_plan` 每次调用都刷新，命中缓存也刷新）：
        # 结构签名复用它，一帧只算一遍 `_plan_inputs`（那里有度量指纹与屏查询）
        self._plan_inputs_seen: tuple | None = None
        self._plan = None
        # 折叠态（收缩流水线折进 +N 的段 id）：采样订阅跟着它走——看不见的段
        # 不该继续烧 psutil/探测包（见 _sync_fold_sampling）。
        self._folded_metrics: frozenset[str] = frozenset()
        # 上一次同步时的停靠细条态（P2 Slice E6）：细条一样是"看不见"，
        # 采样同样全停（见 _sync_content_sampling）。
        self._strip_sampling_off = False
        # 设备槽订阅的影子计数（订阅集合自己记一份：底座只回报计数，问不出
        # "这个 key 是不是刚订阅的"），用于"恢复首轮显示 --"。
        self._sys_subscribed: set[str] = set()
        self._sys_pending: set[str] = set()
        # 被折前最后一拍是采样故障的保持表（metric_id → 原因），见 ③①
        self._sys_error_hold: dict[str, str] = {}
        # GPU 段：读数来自底座 GPU 那一本账（``subscribe_gpu``），与设备槽各记一份。
        # ``_gpu_subscribed`` 是订阅影子，``_gpu_error_hold`` 是它与设备槽同形的
        # "折叠不埋没故障"保持表，``_gpu_sample`` 是最近一份**这一代**的读数。
        self._gpu_sample: gpu_monitor.GpuSample | None = None
        self._gpu_subscribed = False
        self._gpu_error_hold: dict[str, str] = {}
        # 恢复屏障（两份各自一份）：暂停/退订那一刻底座手里**那一个**快照对象。
        # 恢复后读到同一个对象 = 还是上一代的读数，一律拒收（见 _tick_gpu_slot /
        # _tick_device_slot）——底座 stop() 不清最后一份快照，直接读它就是把历史
        # 冒充成实时。
        self._sys_barrier: object | None = None
        self._gpu_barrier: object | None = None
        # 光标是否在岛内（自记 hover 旗帜，见 expand_card 的 hover 暂停与 enter/leaveEvent）
        self._cursor_inside = False
        # 逐指标的**上一拍告警级**（""|warn|crit，P2 Slice E2）：滞回要的"上一拍"
        # 只活在这里，不进段对象（段是每拍现算的数据，不是累积状态）。折叠态会
        # 停掉被折段的采样，靠它把告警保持住——chip 告警点因此不会一闪即灭。
        self._alert_severity: dict[str, str] = {}
        # 「已知但当前不可用」的段 id → 原因（P2 Slice E5 的机制）：胶囊不占位、
        # 卡片写原因。这是一张**宿主填写的能力表**（新能力占位往这里塞条目即可），
        # 内置段如今都能采，所以它默认空着；GPU 的不可用与否是**当刻读数**说了算
        # （见 ``_gpu_unavailable_reason``），不写死在这张表里。
        self._unavailable_reasons: dict[str, str] = {}
        self._geo_from: QRect | None = None
        self._geo_to: QRect | None = None
        self._geo_t = 0.0

        self._anim_timer = QTimer(self)
        self._anim_timer.setInterval(_ANIM_TICK_MS)
        self._anim_timer.timeout.connect(self._on_anim_tick)
        self._anim_last = 0.0

        self._dock_back_timer = QTimer(self)
        self._dock_back_timer.setSingleShot(True)
        self._dock_back_timer.setInterval(_DOCK_BACK_MS)
        self._dock_back_timer.timeout.connect(self._dock_back)

        self._card_collapse_timer = QTimer(self)
        self._card_collapse_timer.setSingleShot(True)
        self._card_collapse_timer.setInterval(_CARD_AUTO_COLLAPSE_MS)
        self._card_collapse_timer.timeout.connect(self.collapse_card)

        # 卡片开着时的每秒 tick（只刷新"更新时间"/状态文字，收起即停表）
        self._card_tick_timer = QTimer(self)
        self._card_tick_timer.setInterval(_CARD_TICK_MS)
        self._card_tick_timer.timeout.connect(self._on_card_tick)

        self._info_timer = QTimer(self)
        self._info_timer.setInterval(30_000)
        self._info_timer.timeout.connect(self._refresh)
        self._info_timer.start()

        # 网络段（``island_metrics`` 的 network 成员，默认关）：采样在后台线程里跑
        # （psutil 计数器单次调用实测 23ms，不能进 16ms 的动画帧预算），
        # 这里只按探测间隔读一次快照、文本变了才重绘。
        self._net_monitor: net_monitor.NetMonitor | None = None
        self._net_sample: net_monitor.NetSample | None = None
        self._net_timer = QTimer(self)
        self._net_timer.setSingleShot(False)
        self._net_timer.timeout.connect(self._tick_network)

        # 设备槽段（``island_metrics`` 的 cpu/mem 成员，默认关）：与网络段同一套
        # 生命周期纪律（岛可见才采样、段撤掉即收口），但采样器是**共享**的
        # （``SysMonitor`` 一条线程喂两个段），订阅按段 id 计：0 订阅零 syscall。
        self._sys_monitor: sys_monitor.SysMonitor | None = None
        self._sys_sample: sys_monitor.SysSample | None = None
        self._sys_timer = QTimer(self)
        self._sys_timer.setSingleShot(False)
        self._sys_timer.timeout.connect(self._tick_sys)

        # 第三方段（声明清单 + 运行期 ``register_segment``，见 ``island_extension_host``）：
        # 采样走**一条**有界节拍的后台线程（与设备槽同款生命周期纪律：岛可见才采、
        # 段撤掉/被折/隐藏即退订），GUI **既不 read 也不 format**——它只读快照。
        # ``_ext_manifest`` 是可信目录里的静态声明（设置进程与主进程同源），
        # ``_ext_subscribed`` 是订阅影子（恢复订阅 = 新序列，与设备槽同口径）。
        self._ext_manifest: dict[str, object] = {}
        self._ext_seeded_thresholds: set[str] = set()
        self._ext_sampler = None
        self._ext_samples: dict[str, object] = {}
        self._ext_subscribed: set[str] = set()
        self._ext_pending: set[str] = set()
        self._ext_error_hold: dict[str, str] = {}
        # 采样代次屏障：起点那一刻的代次；收到的样本代次不一致一律拒收（迟到不复活）
        self._ext_epoch: int | None = None
        self._ext_timer = QTimer(self)
        self._ext_timer.setSingleShot(False)
        self._ext_timer.timeout.connect(self._on_ext_tick)
        self._refresh_extension_declarations()

        # 游戏帧率段（``island_metrics`` 的 fps 成员，**默认关**）：采集是**进程里唯一
        # 一场** ETW 会话（宿主 ``island_fps.FpsHub`` 按岛计数，多宠共用一个采集器），
        # 段自己的状态机（订阅/屏障/四态/卡片素材）全在 ``pet/island_fps.py``——这里只
        # 留两行接线：配置或可见性一变调 ``sync``、按 1Hz 调 ``poll`` 读快照。读数因此
        # 不进绘制路径（GUI 不做 syscall、不 join 采集线程）。
        self._fps = island_fps.FpsSlot()
        self._fps_timer = QTimer(self)
        self._fps_timer.setSingleShot(False)
        self._fps_timer.setInterval(_FPS_TICK_MS)
        self._fps_timer.timeout.connect(self._tick_fps)

        # 余额峰谷档位切换的准点刷新：单次定时到下一切换边界，刷新后再排下一次。
        self._tier_tick_timer = QTimer(self)
        self._tier_tick_timer.setSingleShot(True)
        self._tier_tick_timer.timeout.connect(self._on_balance_tier_tick)
        self._schedule_next_balance_tier_refresh()

        # 置顶保活接线（见 reassert_topmost）：本进程窗口被激活 = 某个 topmost
        # 窗（聊天窗/设置窗/岛气泡/托盘菜单 popup）刚插到岛上面，这是零轮询里
        # 最精确的时机信号；Qt < 6.5 无 focusWindowChanged 时由
        # applicationStateChanged(Active) 覆盖"本进程回到前台"那一半。
        app = QApplication.instance()
        if app is not None:
            focus_changed = getattr(app, "focusWindowChanged", None)
            if focus_changed is not None:
                focus_changed.connect(self._on_own_window_focused)
            app.applicationStateChanged.connect(self._on_application_state_changed)
            # 退出收口：应用退出**不发** closeEvent（Qt 直接销毁窗口），若不在这里挂一刀，
            # 帧率段的订阅与采集线程就只剩"进程退出 + job 对象兜底"这一条路。挂同一个槽
            # 是幂等的（退订按岛计、重复调用只停一次）。
            app.aboutToQuit.connect(self._release_fps_slot)

        # 卡片建在**所有定时器与采样器就位之后**：卡片高度是实测出来的，而实测要
        # 走段管线（`_capsule_width` → `_row_plan` → 采样订阅同步），早建就会在
        # `_net_timer` 还不存在时碰到订阅分支（见 _plan_inputs / _sync_network_slot）。
        self._build_card()

        self._apply_position()
        self._sync_network_slot()
        self._sync_sys_slot()
        self._sync_extension_slot()
        self._sync_fps_slot()

    # ------------------------------------------------------------ 模式/配置
    @property
    def mode(self) -> str:
        return self._mode

    @property
    def dock_edge(self) -> str:
        # 「靠边半隐藏」总开关关着时，存储的停靠边视为无——停靠判定/细条
        # 绘制/碰撞跳过等所有读取处统一走"不停靠"，不留死状态（开关再开
        # 时存储的边恢复生效）
        if not self._edge_dock_enabled():
            return "none"
        return str(self._cfg.get("dock_edge") or "none")

    def _click_action(self) -> str:
        return str(self._cfg.get("click_action") or "expand")

    def _strip_hidden(self) -> bool:
        """**停靠细条态**：胶囊收成细条、内容一个像素都不画。

        判据与绘制层同源——``paintEvent`` 走 ``_paint_strip`` 的那个分支、
        ``_apply_fixed_size`` 压成细条的那个分支、``_target_rect`` 取细条矩形的
        那个分支用的都是 ``_mode == "docked" and not _hover_peek``。hover 滑出的
        完整胶囊**不算**：那是内容可见、读数在画，采样照跑。

        用途是采样闸门（P2 Slice E6）：看不见的段不采样是性能契约，细条态是
        "全部看不见"的极端，所以它整条收口（退订口径与折叠态同一套）。
        """
        return self._mode == "docked" and not self._hover_peek

    def _hidden_chat_enabled(self) -> bool:
        """桌宠隐藏时的对话气泡开关（点击岛弹气泡 + 回复到达自动预览）。"""
        return bool(self._cfg.get("hidden_chat", True))

    def _event_effects_enabled(self) -> bool:
        return bool(self._cfg.get("event_effects", True))

    def _quiet_also_on_fullscreen(self) -> bool:
        """「全屏应用时自动免打扰」的宿主配置（默认开）。

        ``suppression_active`` 是全应用唯一的免打扰判据，但它的
        ``also_on_fullscreen`` 由**调用方**给（判据自己不读配置，见 quiet_mode
        模块注释）：本层漏读就会在全屏时按默认 True 抑制，用户关掉开关后弹跳
        依旧没有。口径与 ``window_alerts.quiet_blocked`` 一致（那里取 host.cfg）。
        """
        return bool(self.config.get("quiet_also_on_fullscreen",
                                    quiet_mode.DEFAULT_QUIET_ALSO_ON_FULLSCREEN))

    def _net_enabled(self) -> bool:
        """网络段开关：``island_metrics`` 里有没有 network（默认关）。

        网络是**普通指标段**（P1 Slice D1 起）：它和信息槽内容各占各的位置，
        信息槽永远由 ``info_mode`` 决定，不再有"网络接管信息槽"的特殊形态。

        真值只有一个来源：列表的成员关系（P1 Slice C 起）。旧键
        ``island_network_enabled`` 只在归一化阶段单向迁移进列表，这里不再读它；
        非列表值（手改配置 / 桩对象）一律按"没开"处理，不做子串匹配。
        """
        return island_segments.NETWORK in self._metric_ids()

    def _net_interval(self) -> float:
        return net_monitor.clamp_interval(
            self.config.get("network_probe_interval_seconds", net_monitor.DEFAULT_INTERVAL_SECONDS))

    def _net_active(self) -> bool:
        """网络槽的**有效**开关：段在列表里 AND 信息槽可见 AND 没被折 AND 不在细条态。

        信息槽被关掉时一条信息段都画不出来（``_visible_parts`` 的第 3 位），
        继续跑采样线程只是白烧 psutil 的 23ms/秒与探测包——所以"显示信息槽"是
        三态里的一态，不是只看网络段在不在列表里。窄屏收缩把它折进 +N
        （``_folded_metrics``）时同理：看不见的段不采样，展开时再订阅。停靠细条态
        （``_strip_hidden``，P2 Slice E6）是同一逻辑的极端档：那时候整行内容都不画。
        """
        if not self._net_enabled():
            return False
        if island_segments.NETWORK in self._folded_metrics:
            return False
        if self._strip_hidden():
            return False
        _icon, _name, info, _status = self._visible_parts()
        return bool(info)

    def _edge_dock_enabled(self) -> bool:
        return bool(self._cfg.get("edge_dock", True))

    def _opacity(self) -> float:
        """背景不透明度倍率（0.4~1.0），设置页可调；非法值回退 1.0。"""
        try:
            value = float(self._cfg.get("opacity", 1.0))
        except (TypeError, ValueError):
            return 1.0
        return max(0.4, min(1.0, value))

    def _accent_color(self) -> QColor:
        key = str(self._cfg.get("accent") or _DEFAULT_ACCENT)
        return QColor(_ACCENT_PRESETS.get(key, _ACCENT_PRESETS[_DEFAULT_ACCENT]))

    # ------------------------------------------------------------ 对外
    def reassert_topmost(self) -> bool:
        """把岛重新插到 topmost 带最上（置顶保活入口）。

        为什么需要：岛带 ``WindowStaysOnTopHint``（Windows = ``WS_EX_TOPMOST``）
        但 ``WindowDoesNotAcceptFocus``（= ``WS_EX_NOACTIVATE``）**从不被激活**，
        而 Windows 的 topmost 带内先后由"谁最近被激活/显示"决定——任何被激活的
        topmost 窗（本进程聊天窗/设置窗/岛对话气泡，或第三方置顶工具）都会盖在
        岛上面；Windows 又不提供"你被盖住了"的通知，岛不主动重申就永远回不来
        （Qt 也只在 flags 变化时写一次 ``WS_EX_TOPMOST``，``show()`` 对已可见的
        窗口是 no-op，所以"设置里保存一次"并不会把它顶回去）。

        触发点全部事件驱动或复用既有通道，不新增定时器/线程：
        ``showEvent``、本进程窗口被激活（前台/焦点信号）、单击岛、
        既有信息刷新心跳（``_refresh``：30s 心跳 + 余额/配置刷新各一次）。
        隐藏态直接返回 False——可见与否由用户决定，隐藏即静默。
        """
        if not self.isVisible():
            return False
        if _native_reassert_topmost(self):
            return True
        # macOS 上 QWidget.raise_() 会激活 accessory 应用，把用户正在输入的
        # 主窗口切走；WindowStaysOnTopHint 已由 Cocoa 负责维持，不再用 Qt
        # raise_() 重排窗口。这个分支必须在真实 macOS 上保持无副作用。
        if sys.platform == "darwin":
            return True
        # Linux：保留既有 Qt 层兜底。
        self.raise_()
        return True

    def _on_own_window_focused(self, window) -> None:
        """本进程有窗口被激活 → 把岛顶回去（``focusWindowChanged``）。

        只接本进程侧的信号：``focusWindowChanged`` 只报告本进程的窗口，
        焦点落到别的应用时它带 ``None``（Qt 语义：焦点移出本应用）——那种情况
        属于"外部应用抢到前台"，不重申（见 ``_on_application_state_changed``）。
        """
        if window is None:
            return
        self.reassert_topmost()

    def _on_application_state_changed(self, state) -> None:
        """本进程成为前台（Active）才重申；失去前台不动作。

        外部应用抢到前台（开始菜单、全屏游戏、第三方置顶工具）时重申会让岛
        反过来盖住系统级置顶面，与 legacy「全屏应用会暂时盖住桌宠」同口径。
        """
        if state == Qt.ApplicationState.ApplicationActive:
            self.reassert_topmost()

    def set_icon_provider(self, fn) -> None:
        """注入"鱼本体头像"取图回调（AppShell 提供：取首个桌宠窗的当前帧图标）。

        回调返回 QPixmap 或 None（帧尚未就绪，岛侧下次重建内容层时重试）。
        注入即作废已缓存头像/内容层：换形象后要立刻取到新头像。
        """
        self._icon_provider = fn if callable(fn) else None
        self._clear_icon_cache()
        self.update()

    def set_identity_provider(self, fn) -> None:
        """注入**角色身份**取源（AppShell 提供：当前呈现身份的每宠配置视图）。

        ``character`` 是每宠键，真值在 slot 档案里；本窗口持有的 ``config`` 是
        进程级配置，只给全局设置用。全局面那个 ``character`` 是出厂默认的残留
        （slot0 迁移与 ``Config.save`` 都会把每宠键从 ``config.json`` 清掉），
        拿它取名字 = 换完角色岛名还停在默认那只。回调每次取名时现取，所以换角色
        无需重新注入；未注入时沿用 ``self.config``（直接构造岛的场合）。
        """
        self._identity_provider = fn if callable(fn) else None

    def set_balance_info(self, tier_text: str, balance_text: str) -> None:
        self._balance_tier_text = str(tier_text or "余额峰谷 --")
        self._balance_text = str(balance_text or "余额 --")
        self._sync_card_labels()
        self._refresh()

    def set_pet_visible(self, visible: bool) -> None:
        self._pet_visible = bool(visible)
        callback = self.on_pet_visibility_changed
        if callable(callback):
            try:
                callback(bool(visible))
            except Exception:
                pass
        # 隐藏桌宠会把**非工作态**的点变成空心环（工作态压过隐藏，见
        # _status_dot_state）——凡点到空心环的路径都该把环一起收掉，这里搭同一趟车
        self._sync_sonar_ring()
        self._sync_card_labels()
        self.update()

    def set_chat_available(self, available: bool) -> None:
        """写入本构建是否具备聊天能力（AppShell 按 enable_chat 喂入）。

        纯桌宠版（无 pet.chat 的打包变体）置 False：hidden_chat 开且桌宠
        隐藏时，单击路由回退为展开卡片——卡片的「显示桌宠」是该形态下
        唯一的恢复入口；否则单击被路由到不存在的对话气泡，岛无任何
        反应，桌宠永远回不来（纯桌宠版实机死锁）。
        """
        self._chat_available = bool(available)

    def set_agent_active(self, active: bool) -> None:
        """旧的粗粒度活跃位（迁移期兼容层）：True → 工作态，False 下降沿 → 完成瞬态。

        新五态体系由 :meth:`set_dsh_state`（真八态收敛，含 success/error/waiting）
        驱动，本方法只服务未升级的调用方。粗粒度通道只有「活跃/非活跃」两个取值，
        分不出 success/idle/error——下降沿是它唯一能提供的「任务结束」边沿，故按
        **完成**渲染（绿点 4s 后回落空闲）；旧的「可见绿灯」语义已废弃。
        """
        active = bool(active)
        rising = active and not self._agent_active
        falling = (not active) and self._agent_active
        self._agent_active = active
        if rising:
            self._bounce(impulse=3.0)
        if falling:
            self._arm_dot_done()
        self._sync_sonar_ring()   # 旧通道同样进工作态：环与状态点同源同判据
        self.update()

    def set_dsh_state(self, state: str) -> None:
        """DSH 八态收敛态注入（五态状态点的权威输入，AppShell 按边沿喂入）。

        事件源是既有派发链（``pet/dsh_state.py`` 收敛 → ``DshMonitor`` →
        ``AgentLinkManager.dsh_state_changed`` → AppShell），这里**不**另开订阅、
        不读原始事件流；待确认/待回答读的就是收敛器的 waiting 锁存位（gated：
        只有带 requestId 的真 UI 级请求才到得了这个位）。

        只做三件事：记态、完成瞬态起停、工作上升沿弹一下。**不动几何**——状态点
        是纯绘制态，永不进宽度白名单键（见 :meth:`_content_cache`）。
        """
        value = str(state or "").strip()
        if value == self._dsh_state:
            return
        previous = self._dsh_state
        self._dsh_state = value
        work_states = (DshState.WORKING.value, DshState.THINKING.value)
        if value == DshState.SUCCESS.value:
            self._arm_dot_done()
        if self._dot_done and self._status_dot_state() != _DOT_DONE:
            # 被更高优先级状态（错误/待确认/工作）顶掉：立即停表销毁，不留过期绿点
            self._stop_dot_done_timer()
        if (value in work_states and previous not in work_states
                and self._event_effects_enabled()
                and not quiet_mode.shared_quiet_state().suppression_active(
                    also_on_fullscreen=self._quiet_also_on_fullscreen())):
            # 事件弹跳过两道闸：event_effects 开关（与 notify_event 同一道）+
            # 免打扰安静规则（裁决稿 §一-7：禁弹跳，读数照常——这条比
            # notify_event 多一道，是该裁决对本路径的单独要求；状态点本身不受
            # 闸门影响。免打扰判据复用全应用唯一的 suppression_active，全屏那一半
            # 的口径按宿主配置走——判据不自己读配置，漏传就等于把用户关掉的
            # 「全屏时自动免打扰」按默认值压回来）
            self._bounce(impulse=3.0)
        # 声纳环跟状态点同源起停（工作态才有）：状态点是"在用"的静态通道，
        # 环是它的节拍通道，两者共用 _status_dot_state 这一个判据
        self._sync_sonar_ring()
        self.update()

    def _arm_dot_done(self) -> None:
        """进入完成瞬态：绿点亮起 4s（单次 QTimer）。

        隐藏态不点表（隐藏即休眠：隐藏期间零定时器空转，绿点也没人看得见）。
        """
        if not self.isVisible():
            return
        self._dot_done = True
        timer = self._dot_done_timer
        if timer is None:
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(self._on_dot_done_timeout)
            self._dot_done_timer = timer
        timer.start(_DOT_DONE_MS)

    def _on_dot_done_timeout(self) -> None:
        """完成瞬态到点：回落当刻应有状态（空闲/隐藏/工作…）。"""
        self._stop_dot_done_timer()
        self.update()

    def _stop_dot_done_timer(self) -> None:
        """停表并销毁完成瞬态定时器（幂等）：回落后不留空转表。"""
        timer, self._dot_done_timer = self._dot_done_timer, None
        self._dot_done = False
        if timer is not None:
            timer.stop()
            timer.deleteLater()

    # ------------------------------------------------------ 工作态声纳环
    def _sonar_eligible(self) -> bool:
        """声纳环的唯一判据（起停都问它，所以不可能出现"两处口径打架"）。

        四条缺一不可：窗口可见、自由胶囊态（展开卡片/停靠细条都不播——卡片态
        读者在别处，细条是另一种图元语言）、状态点开着、当刻状态点就是工作中。
        """
        if not self.isVisible() or self._mode != "normal":
            return False
        if not self._visible_parts()[3]:
            return False
        return self._status_dot_state() == _DOT_WORKING

    def _sync_sonar_ring(self) -> None:
        """按当刻判据起停声纳环（幂等）：状态变了、显隐变了、模式变了都喊它。

        与"只在 working 上升沿起表"相比，幂等口径多兜住一种情形——隐藏时进入
        工作态、显示回来时工作态还在（上升沿早过去了），此时该接着播。
        """
        if self._sonar_eligible():
            self._start_sonar_ring()
        else:
            self._stop_sonar_ring()

    def _start_sonar_ring(self) -> None:
        """起表（幂等）：已在跑就不重开——重开会把相位重置成 0，节拍被踩碎。"""
        timer = self._sonar_timer
        if timer is None:
            timer = QTimer(self)
            timer.setInterval(_SONAR_TICK_MS)
            timer.timeout.connect(self._on_sonar_tick)
            self._sonar_timer = timer
            self._sonar_started = time.monotonic()
        if not timer.isActive():
            timer.start()

    def _on_sonar_tick(self) -> None:
        """每 tick 重判一次判据：不成立就自己停表收口（不指望每个改动点都记得喊停）。"""
        if not self._sonar_eligible():
            self._stop_sonar_ring()
            return
        self.update()

    def _stop_sonar_ring(self) -> None:
        """停表并销毁声纳环定时器（幂等）：离开工作态就不留空转表。"""
        timer, self._sonar_timer = self._sonar_timer, None
        if timer is not None:
            timer.stop()
            timer.deleteLater()

    def set_last_message(self, text: str) -> None:
        """记录最近一条 AI 消息（展开卡片里显示摘要）。

        摘要行数会随文案变化（最多两行），卡片开着时高度得跟着——高度是实测的，
        消息长了卡片就该高一点（k3 §5.2 的"按内容实测"在消息区同样成立）。
        """
        self._last_message = str(text or "").strip()
        self._sync_card_labels()
        self._resize_card_if_needed()

    def notify_event(self, kind: str = "generic") -> None:
        """事件动效入口：reply / balance / tier / generic。尊重 event_effects 开关。"""
        if not self._event_effects_enabled():
            return
        impulse = {"reply": 5.0, "balance": 3.5, "tier": 4.0}.get(kind, 3.0)
        self._bounce(impulse=impulse)
        if kind == "reply":
            self._breathe_until = time.monotonic() + _BREATHE_AFTER_EVENT_S
            self._ensure_anim_timer()

    def bump(self, strength: float = 1.0, dir_x: float = 0.0, dir_y: float = 0.0) -> None:
        """被桌宠撞到（果冻墙→水波感）：撞击点荡开涟漪 + 轻微定向踢动。

        dir_x/dir_y 为岛被顶着的方向（单位向量，由碰撞侧从对方 dv 取反得到）；
        位移与形变只作用于绘制层，窗口几何不动（岛的位置永远由用户拖拽决定）。
        """
        if not self._event_effects_enabled():
            return
        strength = max(0.2, min(float(strength), 3.0))
        # 史莱姆受击：表面压扁（弹簧回摆出果冻抖动）+ 轻微定向踢动/倾斜。
        # 踢动速度钳制：窗口只有 44px 高，多鱼同撞叠加时位移超窗会被裁掉
        squish_v = self._squish_v - 1.15 * strength
        self._squish_v = max(-_SQUISH_V_MAX, min(_SQUISH_V_MAX, squish_v))
        self._kick_vx = max(-150.0, min(150.0, self._kick_vx + float(dir_x) * 110.0 * strength))
        self._kick_vy = max(-150.0, min(150.0, self._kick_vy + float(dir_y) * 110.0 * strength))
        tilt_v = self._tilt_v + 13.0 * strength * (1 if dir_x >= 0 else -1)
        self._tilt_v = max(-_TILT_V_MAX, min(_TILT_V_MAX, tilt_v))
        # 单次撞击动效的动画时间上限从这一击重新起算（连撞 = 每击各有一段动画，
        # 但最后一击之后最多再跑 _BUMP_ANIM_MAX_S）
        self._bump_anim_left = _BUMP_ANIM_MAX_S
        self._ensure_anim_timer()

    def refresh_from_config(self) -> None:
        old_edge = self.dock_edge
        old_icon = self._icon_spec()
        self._cfg = _cfg_dict(self.config)
        self._schedule_next_balance_tier_refresh()
        # 配置一变就重扫第三方组件声明（装/卸组件搭配置刷新这趟车），再同步采样与
        # 卡片——顺序在 _sync_card_labels 之前，免得卡片先按旧清单画一遍
        self._refresh_extension_declarations()
        self._sync_card_labels()
        self._sync_network_slot()
        self._sync_sys_slot()
        self._sync_extension_slot()
        self._sync_fps_slot()
        # 头像必须跟着刷新作废：provider 取的是**当前形象**的帧，而角色热切换
        # 只调 refresh_from_config（不动 island 配置），不换缓存就一直挂旧头像
        if self._icon_image_mode() or self._icon_spec() != old_icon:
            self._clear_icon_cache()
        if self.dock_edge != old_edge:
            self._apply_position()
        # 「靠边半隐藏」被关闭时已停靠的岛要复位回正常形态——否则一直是
        # 16px 细条贴边（碰撞结算也随停靠跳过而长期失效）
        if not self._edge_dock_enabled() and self._mode == "docked":
            self._mode = "normal"
            self._hover_peek = False
            self._apply_fixed_size()
            self._apply_position()
        # 采样闸门放在最后：上面两段都可能改 _mode（停靠边切换 / 关掉靠边半隐藏），
        # 早于它们同步就会拿着过期的细条态去收口或恢复（P2 Slice E6）
        self._sync_content_sampling()
        self._sync_sonar_ring()          # 同上：判据含模式与 show_status，都要最后判
        self._refresh()

    # ------------------------------------------------------------ 余额峰谷
    def _balance_tier_display_text(self) -> str:
        """按系统时间直接生成余额峰谷文案：当前档位标签 + 下一档切换时间。

        不依赖余额查询；档位标签使用设置里的高峰/空闲（或梁文峰/梁文谷/自定义）。
        """
        from . import balance as balance_mod

        now = balance_mod.beijing_now()
        peak_label, idle_label = balance_mod.resolve_tier_labels(
            str(self.config.get("balance_tier_labels_mode", "default") or "default"),
            str(self.config.get("balance_tier_label_peak", "") or ""),
            str(self.config.get("balance_tier_label_idle", "") or ""),
        )
        # 保持无参调用以兼容现有测试/调用面（函数内部使用同一北京时间）。
        tier = balance_mod.deepseek_pricing_tier()
        label = peak_label if tier == "peak" else idle_label
        try:
            _next_tier, next_time = balance_mod.next_pricing_switch(now)
            time_text = balance_mod.format_switch_time(now, next_time)
            return f"{label} → {time_text}"
        except Exception:
            return label

    def _on_balance_tier_tick(self) -> None:
        """到达预设档位切换点：刷新显示、播事件动效，并排下一次准点刷新。"""
        self._refresh()
        self.notify_event("tier")
        self._schedule_next_balance_tier_refresh()

    def _schedule_next_balance_tier_refresh(self) -> None:
        """在下一个峰谷切换边界安排一次单发刷新（只在余额峰谷模式启用）。"""
        if str(self._cfg.get("info_mode") or "time") != "balance_tier":
            self._tier_tick_timer.stop()
            return
        try:
            from . import balance as balance_mod

            _next_tier, next_time = balance_mod.next_pricing_switch()
            now = balance_mod.beijing_now()
            delay_ms = max(250, int((next_time - now).total_seconds() * 1000) + 250)
            self._tier_tick_timer.start(delay_ms)
        except Exception:
            self._tier_tick_timer.stop()

    def _refresh(self) -> None:
        """内容变化后立即重算尺寸、夹回屏幕并重绘。"""
        # 复用既有刷新通道重申置顶（30s 信息心跳 + 余额/配置刷新各一次）：系统
        # 事件（explorer 重启/DPI 变更/休眠唤醒，QTBUG-30359）或别的 topmost 窗
        # 抢走带顶后，最迟一个周期内自行回到最上——不为此新增定时器
        self.reassert_topmost()
        if self._mode == "docked" and not self._hover_peek:
            self.update()
            return
        self._update_size()
        self._clamp_to_screen()
        self.update()

    # ------------------------------------------------------------ 网络槽
    def _make_net_monitor(self, interval: float) -> net_monitor.NetMonitor:
        """建采样器（**测试替换点**：替身不碰 psutil、不发探测包）。"""
        return net_monitor.NetMonitor(interval=interval)

    def _sync_network_slot(self) -> None:
        """按配置启停网络槽的采样线程与读取定时器（幂等）。

        有效开关 = 岛可见 AND 显示信息槽 AND 网络槽开着（见 ``_net_active``）；
        任一不满足即停采样——关闭 → 彻底收口（线程停、socket 关、快照丢弃，信息槽
        回到 ``info_mode``），只是暂时不可见 → 保留对象不采样（同 hideEvent 的
        "隐藏即休眠"口径）。采样间隔变化（设置页改
        ``network_probe_interval_seconds``）即时生效，不重建线程。
        """
        if not self._net_active():
            self._pause_network_slot()
            if self._net_monitor is not None:
                self._net_monitor.close()
                self._net_monitor = None
            self._net_sample = None
            return
        interval = self._net_interval()
        if self._net_monitor is None:
            self._net_monitor = self._make_net_monitor(interval)
        else:
            self._net_monitor.set_interval(interval)
        self._net_timer.setInterval(max(200, int(round(interval * 1000))))
        if self.isVisible():
            self._net_monitor.start()
            self._net_timer.start()
        else:
            self._pause_network_slot()

    def _pause_network_slot(self) -> None:
        """停表停线程但保留对象（隐藏/不可见时用；socket 由线程自己收口）。"""
        self._net_timer.stop()
        if self._net_monitor is not None:
            self._net_monitor.stop()

    def _tick_network(self) -> None:
        """读一次采样快照；值没变就什么都不做（静止时零重绘）。

        **刻意不调 ``_update_size``**：网络段盒宽由声明样本定死（见
        ``_NETWORK_BOX_SAMPLE``），读数在盒内右对齐伸缩，几何重算走宽度白名单
        （``_width_key``）——采样值不归它管。也不走 ``_refresh``：那会每秒重申
        一次置顶（原生 SetWindowPos），没必要，30s 心跳已覆盖置顶保活。
        """
        monitor = self._net_monitor
        if monitor is None:
            return
        sample = monitor.snapshot()
        if sample is None or sample == self._net_sample:
            return
        self._net_sample = sample
        self.update()

    # ------------------------------------------------------------ 设备槽（CPU/内存）
    def _make_sys_monitor(self, interval: float) -> sys_monitor.SysMonitor:
        """建共享采样器（**测试替换点**：替身不碰 psutil）。"""
        return sys_monitor.SysMonitor(interval=interval)

    def _sys_metric_ids(self) -> tuple[str, ...]:
        """设备槽的**订阅集合**：``island_metrics`` 里的 cpu/mem 去掉被折进 +N 的。

        保序、去重（列表序 = 优先级），折叠只影响"要不要订阅"——被折的段还在
        配置里，只是看不见了（见 :meth:`_sync_fold_sampling`）。
        """
        return tuple(metric_id for metric_id in self._metric_ids()
                     if metric_id in _SYS_METRIC_IDS
                     and metric_id not in self._folded_metrics)

    def _gpu_metric_enabled(self) -> bool:
        """GPU 段的采样闸门（与设备槽逐条同款）：在列表里、没被折进 +N。

        GPU **不是**设备槽成员：它不在 :data:`_SYS_METRIC_IDS` 里，所以既不占
        psutil 快照、也不会误用 cpu/mem 的 subscribe（那会读到内存百分比）。
        """
        return (island_segments.GPU in self._metric_ids()
                and island_segments.GPU not in self._folded_metrics)

    def _sys_active(self) -> bool:
        """共享底座的**有效**开关：还有 cpu/mem **或** GPU 要采，且信息槽可见、非细条。

        "两者并集"是刻意的：只勾 GPU 时底座同样要起来（GPU 走同一条线程），但
        两本账各订各的——GPU-only 一个 psutil 调用都不发生。
        """
        if self._strip_hidden():
            return False
        return bool(self._sys_metric_ids() or self._gpu_metric_enabled()) \
            and bool(self._visible_parts()[2])

    def _sync_sys_slot(self) -> None:
        """按配置启停共享底座的订阅、采样线程与读取定时器（幂等）。

        订阅**按段**计：段在列表里且没被折就 subscribe / subscribe_gpu，否则退订
        ——0 订阅时底座只空转计时、不产生任何 syscall（关掉/折掉指标段就该真的
        零开销）。段全撤 / 信息槽关闭 → 彻底收口（线程停、快照丢弃）；只是暂时
        不可见 → 保留对象与订阅、停线程（同 hideEvent 的"隐藏即休眠"口径）。采样
        间隔固定 1Hz（不可配，见 ``sys_monitor.clamp_interval``），不重建线程。

        订阅集合自己记一份影子（``_sys_subscribed`` / ``_gpu_subscribed``）：底座
        只回报订阅**计数**，问不出"这个 key 是不是刚订阅的"。刚订阅的段进
        ``_sys_pending``（GPU 直接清掉旧读数），在拿到一份**新快照**之前按四态报
        ``--``——展开恢复的段不显示折叠前的陈旧读数。

        **恢复屏障**（``monitor.start()`` 真的起了线程才算）：把底座此刻手里那一个
        快照对象记成屏障，并把所有在采的段标成待新样本——暂停前那一代的读数不许在
        恢复后冒充实时值（隐藏→显示之间读数会静默过期，这是用户最容易看到的假象）。
        """
        if not self._sys_active():
            self._unsubscribe_sys()
            self._pause_sys_slot()
            if self._sys_monitor is not None:
                self._sys_monitor.close()
                self._sys_monitor = None
            # 收口袋之前：最近一份快照是故障就把故障留住（6.1sol 终审——
            # 「折叠不埋没告警」不能只靠 severity：error 态段的 chip 点会随
            # 快照清空而消失）。故障不是 severity，它是状态；留住后
            # ``_device_segment`` / ``_gpu_segment`` 在无快照且仍被折时如实报 error。
            # **只在有快照时改写保持表**：本分支在同一次刷新里可能被调两遍
            # （折叠同步 + 刷新直连），第二遍快照已是 None，若清空会把
            # 第一遍刚留住的故障抹掉（实测复现）。
            if self._sys_sample is not None:
                if self._sys_sample.state != island_segments.STATE_OK:
                    reason = str(getattr(self._sys_sample, "error_reason", "") or "")
                    # 保持面 = 配置里当前的所有设备段（astra 保底 M2）：共享快照的
                    # 故障同时影响 cpu/mem 两段，先折的段不能因为「收口袋时已不在
                    # 订阅集」就把故障丢了（它曾通过共享快照呈现 error）。
                    self._sys_error_hold = {
                        mid: reason for mid in _SYS_METRIC_IDS
                        if mid in self._metric_ids()}
                else:
                    self._sys_error_hold = {}
            if self._gpu_sample is not None:
                if self._gpu_sample.state != island_segments.STATE_OK:
                    self._gpu_error_hold = {
                        island_segments.GPU:
                            str(getattr(self._gpu_sample, "error_reason", "") or ""),
                    }
                else:
                    self._gpu_error_hold = {}
            self._sys_sample = None
            self._gpu_sample = None
            self._sys_subscribed = set()
            self._gpu_subscribed = False
            self._sys_pending = set()
            self._sys_barrier = None
            self._gpu_barrier = None
            # 滞回记忆**不清**：全折后 chip 告警点只靠它亮（E2）；恢复订阅时
            # 由 enabled 分支的 restoring 清算新序列的账（M3）。
            return
        monitor = self._sys_monitor
        if monitor is None:
            monitor = self._make_sys_monitor(sys_monitor.DEFAULT_INTERVAL_SECONDS)
            self._sys_monitor = monitor
        enabled = self._sys_metric_ids()
        gpu_enabled = self._gpu_metric_enabled()
        restoring = set(enabled) - self._sys_subscribed
        for metric_id in _SYS_METRIC_IDS:
            if metric_id in enabled:
                monitor.subscribe(metric_id)
                if metric_id in restoring:
                    # 恢复订阅 = 新的采样序列（ops5.5 评审 M3）：滞回记忆与故障
                    # 保持都在这一刻清——92 折掉、掉到 85 恢复时按旧记忆会保 crit，
                    # 按新序列是 warn。**折叠（退订）时不清**：被折段的 severity
                    # 是 chip 告警点的唯一来源（E2「折叠不埋没告警」）。
                    self._alert_severity.pop(metric_id, None)
                    self._sys_error_hold.pop(metric_id, None)
            else:
                monitor.unsubscribe(metric_id)
        # GPU 走自己那本账。**只在真的订过/要订时才调底座**：CPU-only 的替身与
        # 真实路径都不该被"顺手问一句 GPU"打扰（那正是"没有段就没有 PDH"的机器化）。
        if gpu_enabled:
            monitor.subscribe_gpu(island_segments.GPU)
            if not self._gpu_subscribed:
                self._alert_severity.pop(island_segments.GPU, None)
                self._gpu_error_hold.pop(island_segments.GPU, None)
                self._gpu_sample = None      # 新序列：折叠前的读数不许端出来
        elif self._gpu_subscribed:
            monitor.unsubscribe_gpu(island_segments.GPU)
        self._sys_pending = ((self._sys_pending | (set(enabled) - self._sys_subscribed))
                             & set(enabled))
        self._sys_subscribed = set(enabled)
        self._gpu_subscribed = gpu_enabled
        self._sys_timer.setInterval(max(200, int(round(monitor.interval * 1000))))
        if self.isVisible():
            if monitor.start():
                # 新线程 = 新序列：屏障 = 底座此刻手里那一份（旧代），所有在采的段
                # 先按"等新样本"处理。旧代晚到的发布已被底座的代隔离拒收。
                self._sys_barrier = monitor.snapshot()
                self._sys_pending |= set(enabled)
                if gpu_enabled:
                    self._gpu_barrier = monitor.gpu_snapshot()
                    self._gpu_sample = None
            self._sys_timer.start()
        else:
            self._pause_sys_slot()

    def _unsubscribe_sys(self) -> None:
        """退掉两本账的全部订阅（收口前的最后一步：订阅计数不许留在底座上）。

        设备槽按 id 退、GPU 按影子退（**没订过就一次都不调底座**：CPU-only 的路径
        不该顺手去问 GPU 那半边 API）。影子同时清零——退了就是退了。
        """
        monitor = self._sys_monitor
        if monitor is None:
            return
        for metric_id in _SYS_METRIC_IDS:
            monitor.unsubscribe(metric_id)
        if self._gpu_subscribed:
            monitor.unsubscribe_gpu(island_segments.GPU)
        self._sys_subscribed = set()
        self._gpu_subscribed = False

    def _pause_sys_slot(self) -> None:
        """停表停线程但保留对象与订阅（隐藏/不可见时用）。"""
        self._sys_timer.stop()
        if self._sys_monitor is not None:
            self._sys_monitor.stop()

    def _tick_sys(self) -> None:
        """读一次快照：设备槽与 GPU **各读各的**（各自判断、各自推进、一次重绘）。

        两边都**绝不调 ``_update_size``**——``CPU 9%→100%`` 只在盒内左扩，不推邻居；
        内容层位图按 ``_content_cache`` 的 key 自行重建。唯一会改宽的是 GPU 的
        ``unsupported`` 段增删，那在 ``_tick_gpu_slot`` 里显式处理。
        """
        changed = self._tick_device_slot()
        if self._tick_gpu_slot():
            changed = True
        if changed:
            self.update()

    def _tick_device_slot(self) -> bool:
        """读一次设备槽快照；值没变就什么都不做。返回是否要重绘。

        新快照到手才清 ``_sys_pending``：刚（重新）订阅的段在这一刻之前一直报
        ``--``——"采样中"与"拿折叠前的旧读数冒充它一直在采"必须分得开。恢复屏障
        （``_sys_barrier``）期间连"值没变"的短路都要让路：那一份可能是旧代的对象，
        必须等真正的新样本。
        """
        monitor = self._sys_monitor
        if monitor is None:
            return False
        sample = monitor.snapshot()
        if sample is None or sample is self._sys_barrier:
            return False
        if sample == self._sys_sample and not self._sys_pending:
            return False
        self._sys_barrier = None
        self._sys_sample = sample
        self._sys_pending.clear()
        self._update_alert_severity(sample)
        return True

    def _tick_gpu_slot(self) -> bool:
        """读一次 GPU 快照；返回是否要重绘。

        与设备槽同款但有两处 GPU 专有：① 屏障拒绝的是**同一个样本对象**（读数器每
        拍发一个新对象，所以"还是那一个"精确等于"上一代"）；②
        ``unsupported ↔ 支持`` 是**段的增删**，跨过这道门要重算窗口几何（读数本身
        永远不改宽）。
        """
        monitor = self._sys_monitor
        if monitor is None or not self._gpu_metric_enabled():
            return False
        sample = monitor.gpu_snapshot()
        if sample is None or sample is self._gpu_barrier:
            return False
        if sample == self._gpu_sample:
            return False
        self._gpu_barrier = None
        unavailable_before = bool(self._gpu_unavailable_reason())
        self._gpu_sample = sample
        self._update_gpu_alert(sample)
        if unavailable_before != bool(self._gpu_unavailable_reason()):
            self._update_size()
        return True

    def _update_alert_severity(self, sample) -> None:
        """按新快照推进各指标的告警级（滞回要的"上一拍"状态，P2 Slice E2）。

        必须在 **GUI 读取快照的那一刻**算（``_tick_sys`` 是读数的唯一入口），不能在
        绘制时惰性算：两次读数之间不绘制是常态（窗口隐藏、值没变不 update），
        惰性算会把中间那拍整个漏掉，滞回退化成"每次都从零开始"——92 降 91 会被
        误判成首次越过 warn 线而画成黄，正好是滞回要防的事。

        告警语义是**按 UI 读取拍**而不是按后台每份采样（R1 评审裁决）：后台采样
        只留最新一份快照，GUI 1Hz 定时器来取——GUI 繁忙时后台连发两拍（92→91）
        只会被读到 91。这是刻意的：**告警色跟随用户实际看到的读数**（没看到 92
        就不该按 92 上色），与显示值永远一致；要"每份采样都推进"得在采样端维护
        告警状态，那是另一条路，本版本不取。

        折叠不影响这里：快照是 cpu/mem 共享的，被折段的读数照旧推进它的告警级
        （哪怕这一段自己已经退订、显示退回 ``--``）——chip 告警点靠这条活着。
        快照报 error（读数全 None）时跳过：无读数不褪色，保持上一拍。
        """
        if sample.state != island_segments.STATE_OK:
            return
        for metric_id in _SYS_METRIC_IDS:
            value = (sample.cpu_percent if metric_id == island_segments.CPU
                     else sample.mem_percent)
            self._alert_severity[metric_id] = island_segments.alert_severity(
                metric_id, value, self._alert_severity.get(metric_id, ""))

    def _update_gpu_alert(self, sample) -> None:
        """按新 GPU 快照推进 GPU 段的告警级（与设备槽共用同一张滞回表）。

        GPU **没有声明阈值**（``island_segments.ALERT_THRESHOLDS`` 里没有它）：最忙
        引擎 100% 是"有个引擎跑满了"，不是故障，给 CPU 那条线是伪精确——这里照走
        同一条判定链，所以"没阈值恒无色"是判定链自己给的结论，不是这里的分支。
        将来产品要数值告警，只需往阈值表登记 GPU，这里的账就自动跟上。

        非 ``ok`` 拍不推进也不褪色（与 ``_update_alert_severity`` 同口径）：无读数
        不等于"降到安全值"。error 态在**折叠**时由 ``island_layout._folded_alert``
        按 crit 计——那是 chip 告警点的事，不在这里。
        """
        if sample.state != island_segments.STATE_OK:
            return
        self._alert_severity[island_segments.GPU] = island_segments.alert_severity(
            island_segments.GPU, sample.percent,
            self._alert_severity.get(island_segments.GPU, ""))

    # ------------------------------------------------------------ 游戏帧率段
    def _fps_metric_enabled(self) -> bool:
        """帧率段的采样闸门（与 GPU 逐条同款）：在 ``island_metrics`` 里、没被折进 +N。"""
        return (island_segments.FPS in self._metric_ids()
                and island_segments.FPS not in self._folded_metrics)

    def _fps_active(self) -> bool:
        """帧率段的**有效**开关：还有这一段要采，且信息槽可见、不是停靠细条。

        与 ``_sys_active`` 同口径（细条态内容一个像素都不画 = 全停采样）：帧率段独
        占一整场 ETW 采集会话，比设备槽更该守"看不见就不采"。
        """
        if self._strip_hidden():
            return False
        return self._fps_metric_enabled() and bool(self._visible_parts()[2])

    def _fps_target(self) -> tuple[str, str]:
        """当前的目标配置 ``(跟随方式, 指定程序名)``——**进程级**键，直接读配置。

        两个键都是全局设置（不在 ``PER_SLOT_SETTING_KEYS`` 里），子宠的
        ``SlotRoutedConfig`` 也读主配置那份，所以多宠共用同一个目标；改了就即时
        生效（``FpsMonitor.configure`` 换代重解析目标，旧代读数被代次闸门丢弃）。
        """
        return (str(self.config.get("game_fps_target_mode") or "foreground"),
                str(self.config.get("game_fps_process") or ""))

    def _sync_fps_slot(self) -> None:
        """按配置与可见性驱动帧率段（幂等）。

        与 ``_sync_sys_slot`` 的三条纪律逐条对齐：① 段不在列表里 / 被折 / 细条 /
        信息槽关 → 退订收口（看不见的段不该继续烧 ETW）；② 岛不可见只是停线程、
        保留订阅（隐藏→显示按同一路径恢复）；③ 恢复时先清旧值，且**只认新序列的
        第一份新读数**（屏障在 ``FpsSlot`` 里）。

        段自己的状态机全在 ``pet/island_fps.py``：这里既不管订阅计数，也不管"哪一
        份快照该拒收"——多宠共用一个采集器，那些账必须集中在一处。
        """
        mode, process = self._fps_target()
        self._fps.sync(enabled=self._fps_active(), visible=self.isVisible(),
                       mode=mode, process=process)
        if self.isVisible() and self._fps_active():
            self._fps_timer.start()
        else:
            self._fps_timer.stop()

    def _pause_fps_slot(self) -> None:
        """隐藏即休眠：停读表与采集线程，**保留订阅**（显示回来幂等恢复）。"""
        self._fps_timer.stop()
        self._fps.pause()

    def _release_fps_slot(self) -> None:
        """最终收口（窗口销毁 / 应用退出）：停表 + 停线程 + 退订（幂等、**不 join**）。

        退订按岛计：还有别的岛在看读数时，这一个岛的收口不会把采集链路带走。收口不在
        GUI 线程上等采集进程退出（有界等待在采集线程自己那边），所以"关个岛/退出应用"
        不会被 PresentMon 的会话收口吊住。
        """
        self._fps_timer.stop()
        self._fps.release()

    def _tick_fps(self) -> bool:
        """读一次帧率快照（1Hz）；返回是否要重绘。

        与 ``_tick_gpu_slot`` 同款，但"段增删"来自两处：采集器报 ``unsupported``
        （内置采集程序不在）与平台闸门（非 Windows）——两者都由
        ``island_fps.FpsSlot.unavailable_reason`` 说，所以这里只按它变没变决定要不
        要重算几何。**读数本身永远不改宽**。
        """
        if not self._fps_active():
            return False
        result = self._fps.poll()
        if result.occupying_changed:
            self._update_size()
        return result.changed

    # ------------------------------------------------------------ 第三方段（声明清单 + 运行期注册）
    def _make_extension_sampler(self, interval: float):
        """建第三方段采样器（**测试替换点**：替身不起线程、不 import 组件）。"""
        return island_extension_host.ExtensionSampler(interval=interval)

    def _refresh_extension_declarations(self) -> None:
        """重扫可信组件声明（安装 / 卸载组件后由配置刷新与新样本发现）。

        静态 metadata（JSON）与**运行期 import** 是两件事：这里只读清单，所以
        由它决定的呈现（段名 / 盒宽样本 / 单位 / 卡片来源）不需要执行组件代码，
        与独立设置进程读的是同一份真值。声明的阈值并进**同一张**告警表
        （与 CPU/内存同一条判定链）；清单里消失的组件把阈值收回来——卸载 = 撤声明、
        撤阈值，不留第二真值。
        """
        manifest: dict[str, object] = {}
        try:
            for declaration in island_extension_host.declarations():
                manifest[declaration.segment_id] = declaration
        except Exception:  # pragma: no cover - 目录不可读等极端环境
            logger.warning("灵动岛组件声明清单读取失败", exc_info=True)
        self._ext_manifest = manifest
        for segment_id, declaration in manifest.items():
            thresholds = getattr(declaration, "thresholds", None)
            if thresholds:
                island_segments.seed_thresholds(segment_id, thresholds)
                self._ext_seeded_thresholds.add(segment_id)
        for segment_id in tuple(self._ext_seeded_thresholds - set(manifest)):
            island_segments.drop_thresholds(segment_id)
            self._ext_seeded_thresholds.discard(segment_id)

    def _extension_declaration(self, segment_id: str):
        """这一段的声明（可呈现的静态面）：**运行期注册面优先**，其次清单里的组件。

        两条路都给同一个形状（``label`` / ``max_text`` / ``unit`` / ``unit_full`` /
        ``source`` / ``detail``），所以胶囊与卡片只有一套取字段的代码。F1 就存在的
        ``register_segment`` 内存接口因此照样能上岛——它只是**不能**决定配置准入
        （准入看清单，见 ``island_extension_host.allowed_metric_ids``），
        "能显示"与"能写进配置"是两件事，配置保存因此不依赖进程内的注册顺序。
        """
        registered = island_segments.get_segment(segment_id)
        if registered is not None:
            return registered
        return self._ext_manifest.get(segment_id)

    def _extension_executable(self, segment_id: str) -> bool:
        """这一段现在**能采**吗：注册过，或清单里声明了且 provider 文件在。

        暂缺 provider 的组件（清单在、文件不在）**保留用户偏好、不执行**——它既不
        采样也不占位（见 :meth:`_unavailable_segments`），卡片写原因；文件回来
        （下一次重扫）即恢复。
        """
        if island_segments.get_segment(segment_id) is not None:
            return True
        declaration = self._ext_manifest.get(segment_id)
        return bool(declaration is not None
                    and getattr(declaration, "provider_present", False))

    def _extension_metric_ids(self) -> tuple[str, ...]:
        """配置里有声明的第三方段 id（保序；内置段不在此列——它们各有自己的采样账）。"""
        return tuple(
            metric_id for metric_id in self._metric_ids()
            if metric_id not in island_segments.BUILTIN_SEGMENT_IDS
            and self._extension_declaration(metric_id) is not None)

    def _extension_enabled_ids(self) -> tuple[str, ...]:
        """要采的段：有可执行声明、且没被折进 +N（看不见的段不采样）。"""
        return tuple(metric_id for metric_id in self._extension_metric_ids()
                     if self._extension_executable(metric_id)
                     and metric_id not in self._folded_metrics)

    def _extension_active(self) -> bool:
        """第三方段采样器的有效开关：还有要采的段，且信息槽可见、非停靠细条。"""
        if self._strip_hidden():
            return False
        return bool(self._extension_enabled_ids()) and bool(self._visible_parts()[2])

    def _sync_extension_slot(self) -> None:
        """按配置启停第三方段采样（幂等；与设备槽逐条同款的生命周期纪律）。

        订阅按段 id 计：段在配置里、有可执行声明、且没被折 → 订阅，否则退订；
        全撤 / 信息槽关闭 / 细条态 → 收口（**停线程、丢快照、清订阅账**，但**保留
        采样器对象**）；只是暂时不可见 → 保留对象与订阅、停线程（同 hideEvent 的
        "隐藏即休眠"口径）。

        **为什么收口不销毁采样器对象**：``stop()`` 杀不掉卡在组件 ``read`` 里的线程
        （Python 没有这种能力），那个 worker 会在 ``read`` 返回后自行退出。丢掉对象、
        重新启用时再造一个，就会**两条 worker 同时跑同一个 provider**（线程数失去
        上界）。留着实例，重新启用时 ``start()`` 接续那条还在的 worker（见
        ``island_extension_host.ExtensionSampler.start``）：实例同一、线程唯一、
        段 token 连续，恢复后自动继续采当刻订阅。

        **表与采样一起起/一起停**：可见且采样器在跑就必须有人读它的快照，否则读数
        永远停在宿主外（表只在 ``_pause_extension_slot`` / ``hideEvent`` 停）。

        **恢复订阅 = 新序列**：清这一段旧的告警滞回记忆与故障保持，并把它标进
        ``_ext_pending``——拿到这一代的第一份新样本之前一律按 ``--``（采样中），
        不拿折叠前的旧读数冒充"它一直在采"。代次屏障（``_ext_epoch``）拦的是更早
        一代在途结果的迟到复活。
        """
        if not self._extension_active():
            self._unsubscribe_extensions()
            self._pause_extension_slot()
            # 收口袋：最近一拍是故障就把它留住（折叠不埋没故障——chip 告警点与卡片
            # 原因行靠它活着）。只在有样本时改写保持表（本分支一次刷新里可能被调两遍）。
            if self._ext_samples:
                self._ext_error_hold = {
                    segment_id: str(getattr(sample, "error_reason", "") or "")
                    for segment_id, sample in self._ext_samples.items()
                    if getattr(sample, "state", "") not in (
                        island_segments.STATE_OK, island_segments.STATE_SAMPLING)
                }
            self._ext_samples = {}
            self._ext_subscribed = set()
            self._ext_pending = set()
            self._ext_epoch = None
            # 滞回记忆**不清**：全折后 chip 告警点只靠它亮（与设备槽同口径）。
            return
        sampler = self._ext_sampler
        if sampler is None:
            sampler = self._make_extension_sampler(
                island_extension_host.DEFAULT_INTERVAL_SECONDS)
            self._ext_sampler = sampler
        enabled = self._extension_enabled_ids()
        wanted = set(enabled)
        restoring = wanted - self._ext_subscribed
        for segment_id in enabled:
            sampler.subscribe(segment_id)
        for segment_id in tuple(self._ext_subscribed - wanted):
            sampler.unsubscribe(segment_id)
            self._ext_samples.pop(segment_id, None)   # 退订即丢它的读数：迟到不复活
        for segment_id in restoring:
            self._alert_severity.pop(segment_id, None)
            self._ext_error_hold.pop(segment_id, None)
            self._ext_samples.pop(segment_id, None)
        self._ext_pending = (self._ext_pending | restoring) & wanted
        self._ext_subscribed = wanted
        self._ext_timer.setInterval(max(200, int(round(sampler.interval * 1000))))
        if self.isVisible():
            if sampler.start():
                # 新的那一条采样线程 / 接续回来的同一条 = 新一代：屏障记下它，
                # 早先那一代的在途样本一律拒收
                self._ext_epoch = sampler.epoch
                self._ext_samples = {}
                self._ext_pending |= wanted
            # 表随采样一起起（与网络槽同款；重复 start 只是重置节拍，无副作用）：
            # 少这一步就是"线程在采、读数没人读"——第三方段永远停在 --
            self._ext_timer.start()
        else:
            self._pause_extension_slot()

    def _unsubscribe_extensions(self) -> None:
        """退掉全部第三方段订阅（收口前的最后一步：订阅数不许留在采样器上）。"""
        sampler = self._ext_sampler
        if sampler is None:
            return
        for segment_id in tuple(self._ext_subscribed):
            sampler.unsubscribe(segment_id)
        self._ext_subscribed = set()

    def _pause_extension_slot(self) -> None:
        """停表停线程但保留对象与订阅（隐藏 / 不可见时用）。"""
        self._ext_timer.stop()
        if self._ext_sampler is not None:
            self._ext_sampler.stop()

    def _on_ext_tick(self) -> None:
        """第三方段的采样表节拍：快照有变化就请求一次重绘（与 ``_tick_sys`` 同款）。

        ``_tick_extension`` 只 ``return changed``，而 Qt 的 ``timeout`` 不消费返回值——
        直接把回调接过去，读数进了 ``_ext_samples`` 也不会有一个像素落地（第三方-only
        的静止胶囊要等 30s 心跳或鼠标事件偶然救场）。所以返回值必须在这里被消费掉。

        **只请求重绘**：不调 ``_update_size``、不重申置顶、不动窗口几何——盒宽由声明的
        ``max_text`` 定死（``_width_key`` 不含采样值），读数只在盒内伸缩；卡片开着时由
        它自己的每秒 tick（``_on_card_tick``）同步，这条通道只负责胶囊。
        """
        if self._tick_extension():
            self.update()

    def _tick_extension(self) -> bool:
        """读一次第三方段快照（**只读**：GUI 不 read 也不 format）。

        三处拒收，都是"不伪造读数"的机器化：① 采样器换代后的旧代结果；② 已退订
        段的迟到结果；③ 还没到第一份新样本的段（``_ext_pending``）按四态报 ``--``。

        与设备槽的差别只有一处：第三方段**没有**共享快照，退订就等于没有读数，
        所以被折段的读数不再推进（chip 告警点靠滞回记忆与故障保持亮着）。
        """
        sampler = self._ext_sampler
        if sampler is None or self._ext_epoch is None:
            return False
        epoch = sampler.epoch
        if epoch != self._ext_epoch:
            return False        # 旧代：这一份结果永久作废（不复活）
        snapshot = sampler.snapshot()
        current: dict[str, object] = {}
        changed = False
        for segment_id, sample in snapshot.items():
            if getattr(sample, "epoch", None) != epoch:
                continue
            if segment_id not in self._ext_subscribed:
                continue
            if segment_id in self._ext_pending and \
                    getattr(sample, "state", "") != island_segments.STATE_SAMPLING:
                self._ext_pending.discard(segment_id)
            previous = self._ext_samples.get(segment_id)
            if previous is None or previous.display_key != sample.display_key:
                changed = True
            if self._update_extension_alert(segment_id, sample):
                changed = True
            current[segment_id] = sample
        if set(current) != set(self._ext_samples):
            changed = True
        self._ext_samples = current
        return changed

    def _update_extension_alert(self, segment_id: str, sample) -> bool:
        """按新快照推进这一段的告警级（滞回要的"上一拍"在 ``_alert_severity``）。

        判定链与 CPU/内存**完全同一条**（``island_segments.alert_severity`` +
        声明的 ``(warn, crit)`` 阈值）；原始值来自快照的 ``raw_value``（不是文案——
        文案是给人看的，阈值要的是数）。0 是**真读数**（进判定链），``None`` / NaN /
        非 ok 拍一律不推进也不褪色（"还没读数"不等于"降到安全值"）。
        返回告警级是否变了（变了要重绘：色是呈现面）。
        """
        if getattr(sample, "state", "") != island_segments.STATE_OK:
            return False
        raw = getattr(sample, "raw_value", None)
        if raw is None:
            return False
        previous = self._alert_severity.get(segment_id, "")
        severity = island_segments.alert_severity(segment_id, raw, previous)
        if severity == previous:
            return False        # 同色不算变化（也不写表：没阈值/无色的段不留空条目）
        self._alert_severity[segment_id] = severity
        return True

    def _extension_segment(self, segment_id: str) -> _InfoSeg:
        """第三方段的胶囊形态：``label + 定宽值盒 (+ 单位盒)``，与内置段同一条排版路径。

        盒宽样本 = 组件声明的 ``max_text``（**读数永不进宽度键**：值只在盒内右对齐
        伸缩）；单位取声明值（拆分只用来把单位从数值那半剥掉），所以 ok ↔ 采样态
        之间不会差出一个单位盒宽。非 ``ok`` 态一律同宽 ``--``（``unsupported``
        不占位——那种段根本不在段列表里，见 :meth:`_unavailable_segments`）。
        """
        declaration = self._extension_declaration(segment_id)
        unit = str(getattr(declaration, "unit", "") or "")
        max_text = str(getattr(declaration, "max_text", "") or "")
        sample = self._ext_samples.get(segment_id)
        if sample is None or segment_id in self._ext_pending:
            if segment_id in self._ext_error_hold:
                state, text = island_segments.STATE_ERROR, ""
            else:
                state, text = island_segments.STATE_SAMPLING, ""
        else:
            state = str(getattr(sample, "state", island_segments.STATE_SAMPLING))
            text = str(getattr(sample, "text", "") or "")
        display, _unit = island_segments.split_value_unit(self._state_text(state, text), unit)
        box_text = island_segments.split_value_unit(max_text, unit)[0]
        return _InfoSeg(kind=island_layout.KIND_METRIC, seg_id=segment_id,
                        label=str(getattr(declaration, "label", "") or ""),
                        text=display, box_text=box_text, state=state, unit=unit,
                        severity=self._alert_severity.get(segment_id, ""))

    def refresh_segments(self) -> None:
        """第三方段声明发生变化后由宿主通知：重扫清单并立即重算（幂等）。

        覆盖两条路径：① ``register_segment`` / ``unregister_segment`` 的内存注册面；
        ② 用户往可信目录里装 / 卸组件（配置刷新路径已自动搭车，这里给显式入口）。
        """
        self._refresh_extension_declarations()
        self._sync_extension_slot()
        self._sync_card_labels()
        self._refresh()

    # ------------------------------------------------------------ 卡片四分区（P2 Slice E5）
    def _build_card(self) -> None:
        """展开卡片：概览 / 指标详情 / 最近消息 / 操作 四分区。默认隐藏。

        分区自上而下装进一个固定间距的竖排（间距 0）：每区自带横分隔线与上下
        间距、并被钉上**实测**的固定高（见 :meth:`_sync_card_heights`），所以
        "没有指标段就整区连分隔线一起消失"是免费的，卡片高度也就等于账目的和
        （测试拿 ``layout().sizeHint().height()`` 与 ``_card_height()`` 对账）。
        卡片底板仍由窗口 ``paintEvent`` 统一绘制（圆角连续），容器自身透明。
        """
        self._card_box = QWidget(self)
        self._card_box.setObjectName("dynamic-island-card")
        self._card_box.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._card_box.setStyleSheet("background: transparent;")
        layout = QVBoxLayout(self._card_box)
        layout.setContentsMargins(_CARD_MARGIN_X, _CARD_MARGIN_TOP,
                                  _CARD_MARGIN_X, _CARD_MARGIN_BOTTOM)
        layout.setSpacing(0)

        # ---- A 概览区：主读数（大号）+ 峰谷 + 状态点与状态文字 ----
        self._card_overview = QWidget(self._card_box)
        overview = QVBoxLayout(self._card_overview)
        overview.setContentsMargins(0, 0, 0, 0)
        overview.setSpacing(_CARD_LINE_GAP)
        self._card_balance_label = QLabel(self._card_overview)
        self._card_tier_label = QLabel(self._card_overview)
        self._card_status_row = QWidget(self._card_overview)
        status_line = QHBoxLayout(self._card_status_row)
        status_line.setContentsMargins(0, 0, 0, 0)
        status_line.setSpacing(_CARD_STATUS_DOT_GAP)
        self._card_status_dot = _CardStatusDot(self, self._card_status_row)
        self._card_status_label = QLabel(self._card_status_row)
        status_line.addWidget(self._card_status_dot, 0, Qt.AlignmentFlag.AlignVCenter)
        status_line.addWidget(self._card_status_label, 0, Qt.AlignmentFlag.AlignVCenter)
        status_line.addStretch(1)
        for widget in (self._card_balance_label, self._card_tier_label,
                       self._card_status_row):
            overview.addWidget(widget)

        # ---- B 指标详情区：每段一行（段名 / 当前值合并串 / 单位·来源·更新时间）----
        self._card_metrics = QWidget(self._card_box)
        metrics = QVBoxLayout(self._card_metrics)
        # 只留上边距：下行间距由"分隔线之后的那一档"给（= 线上下各 8px）
        metrics.setContentsMargins(0, _CARD_SECTION_GAP, 0, 0)
        metrics.setSpacing(0)
        self._card_metrics_layout = metrics
        metrics.addWidget(_CardSeparator(self, self._card_metrics))
        metrics.addSpacing(_CARD_SECTION_GAP)
        self._card_row_widgets: list[QWidget] = []
        self._card_row_labels: list[tuple[QLabel, QLabel, QLabel]] = []
        self._card_rows_key: tuple | None = None

        # ---- C 最近消息（沿用现状内容：一行摘要、最多两行）----
        self._card_message_box = QWidget(self._card_box)
        message = QVBoxLayout(self._card_message_box)
        message.setContentsMargins(0, _CARD_SECTION_GAP, 0, 0)
        message.setSpacing(0)
        message.addWidget(_CardSeparator(self, self._card_message_box))
        message.addSpacing(_CARD_SECTION_GAP)
        self._card_message_label = QLabel(self._card_message_box)
        self._card_message_label.setWordWrap(True)
        message.addWidget(self._card_message_label)

        # ---- D 操作行（沿用现状三个按钮，高钉 28px 进账目）----
        self._card_actions = QWidget(self._card_box)
        actions = QVBoxLayout(self._card_actions)
        actions.setContentsMargins(0, _CARD_SECTION_GAP, 0, 0)
        actions.setSpacing(0)
        button_row = QHBoxLayout()
        button_row.setSpacing(8)
        self._card_toggle_btn = QPushButton("显隐桌宠", self._card_actions)
        self._card_chat_btn = QPushButton("聊天", self._card_actions)
        self._card_settings_btn = QPushButton("设置", self._card_actions)
        self._card_toggle_btn.clicked.connect(self._on_card_toggle)
        self._card_chat_btn.clicked.connect(self._on_card_chat)
        self._card_settings_btn.clicked.connect(self._on_card_settings)
        for btn in (self._card_toggle_btn, self._card_chat_btn,
                    self._card_settings_btn):
            button_row.addWidget(btn)
        actions.addLayout(button_row)

        for section in (self._card_overview, self._card_metrics,
                        self._card_message_box, self._card_actions):
            layout.addWidget(section)
        self._card_box.hide()
        self._sync_card_labels()

    def _sync_card_labels(self) -> None:
        """刷新四区文案与结构（展开 / 配置刷新 / 卡片开着时的每秒 tick）。

        结构重建（B 区行控件与各区固定高）只在**结构签名**变化时发生：每秒的
        tick 只改文字，"更新时间"走字时行的骨架与卡片几何一动不动。
        """
        if not hasattr(self, "_card_box"):
            return
        _bg, primary, secondary = self._style_palette()
        primary_color, secondary_color = QColor(primary), QColor(secondary)
        # 字体走字号层（与胶囊同一份源）、颜色走主题函数：卡片不自己发明一套
        self._card_balance_label.setFont(self._font_for("primary"))
        for label in (self._card_tier_label, self._card_status_label,
                      self._card_message_label):
            label.setFont(self._font_for("aux"))
        self._apply_card_style(self._card_balance_label,
                               f"color: {primary_color.name()};")
        self._apply_card_style(self._card_tier_label,
                               f"color: {secondary_color.name()};")
        self._apply_card_style(self._card_status_label,
                               f"color: {self._status_dot_color().name()};")
        self._apply_card_style(self._card_message_label,
                               f"color: {secondary_color.name()};")
        # 按钮：圆角 14px（= 按钮高 28 的一半）、accent 以 rgba 三档穿透做
        # 常态/悬停/按下——常态就是"淡底 + 描边"的次级按钮，不再是隐形按钮；
        # 按下（0.30）比悬停（0.20）更深，反馈不会落回次色灰而断档
        accent = self._accent_color()
        accent_rgb = f"{accent.red()}, {accent.green()}, {accent.blue()}"
        button_style = (
            f"QPushButton {{ color: {primary_color.name()};"
            f" background: rgba({accent_rgb}, 0.10);"
            f" border: 1px solid rgba({accent_rgb}, 0.45);"
            f" border-radius: 14px; padding: 4px 12px; }}"
            f"QPushButton:hover {{ background: rgba({accent_rgb}, 0.20);"
            f" border-color: rgba({accent_rgb}, 0.70); }}"
            f"QPushButton:pressed {{ background: rgba({accent_rgb}, 0.30); }}"
        )
        for btn in (self._card_toggle_btn, self._card_chat_btn,
                    self._card_settings_btn):
            btn.setFont(self._font_for("name"))
            self._apply_card_style(btn, button_style)
            btn.setFixedHeight(_CARD_BUTTON_H)
        self._card_toggle_btn.setText("隐藏桌宠" if self._pet_visible else "显示桌宠")
        # ---- A 概览 ----
        self._card_balance_label.setText(self._balance_text)
        self._card_tier_label.setText(self._balance_tier_text)
        self._card_status_label.setText(self._card_status_text())
        # ---- B 指标详情 ----
        # 行数据**一趟只算一次**（每次都要过段管线与行计划）：往下传，别在
        # 宽度/高度/文案各算一遍——每秒 tick 会走这条路，重复计算是白烧 CPU
        rows = self._card_metric_rows()
        self._sync_card_rows(rows, primary_color, secondary_color)
        # ---- C 最近消息（最多两行，超出右省略）----
        self._card_message_label.setText(self._card_message_display())
        # ---- 各区固定高（账目的落点）----
        self._sync_card_heights(rows)
        self._card_status_dot.update()

    @staticmethod
    def _apply_card_style(widget, style: str) -> None:
        """只在校样式串真的变了才 ``setStyleSheet``。

        Qt 的 ``setStyleSheet`` 不做同值短路：每次都重新 polish 控件（实测 13 个
        控件 ≈ 1.4ms）。卡片每秒 tick 会走一遍样式同步，同值也重设就是白烧 CPU。
        """
        if widget.styleSheet() != style:
            widget.setStyleSheet(style)

    def _card_status_text(self) -> str:
        """概览区的状态文字（E1 五态的字面化；点与文字同源同色）。

        ``_DOT_HIDDEN`` 走两支说法：空心环在胶囊上只说"没有可看的东西"，卡片上
        得说清是桌宠被藏了还是 DSH 没在跑。
        """
        state = self._status_dot_state()
        if state == _DOT_HIDDEN:
            return "桌宠已隐藏" if not self._pet_visible else "DSH 离线"
        return _CARD_STATUS_TEXT.get(state, "")

    def _card_message_text(self) -> str:
        """最近消息原文（空则占位句）——行数与省略都以它为源。"""
        return self._last_message or "（暂无最近消息）"

    def _card_message_display(self) -> str:
        """消息的显示文案：按"两行宽"右省略，配合 wordWrap 与钉死的高度限两行。"""
        return island_layout.elide_to_width(
            self._card_message_text(),
            self._card_content_width() * _CARD_MESSAGE_MAX_LINES,
            self._metrics_for("aux"))

    def _card_message_lines(self) -> int:
        """消息行数（1..``_CARD_MESSAGE_MAX_LINES``）：按真实度量算换行，不写死。"""
        metrics = self._metrics_for("aux")
        bounds = metrics.boundingRect(
            QRect(0, 0, max(1, self._card_content_width()), 0),
            int(Qt.TextFlag.TextWordWrap), self._card_message_text())
        spacing = max(1, metrics.lineSpacing())
        lines = -(-int(bounds.height()) // spacing)
        return max(1, min(_CARD_MESSAGE_MAX_LINES, lines))

    # ------------------------------------------------------------ 卡片账目（高度/宽度）
    def _card_overview_height(self) -> int:
        """A 区高 = 主读数行 + 峰谷行 + 状态行，逐行实测（+ 两处行距）。"""
        metrics = self._metrics_for("aux")
        status_h = max(metrics.height(), int(_STATUS_DOT_PX))
        return (self._metrics_for("primary").height()
                + _CARD_LINE_GAP + metrics.height()
                + _CARD_LINE_GAP + status_h)

    def _card_metrics_height(self, rows=None) -> int:
        """B 区高 = 上下各一档分区间距 + 1px 分隔线 + N×行高；N=0 时整区不占位。"""
        count = len(self._card_metric_rows() if rows is None else rows)
        if not count:
            return 0
        return 2 * _CARD_SECTION_GAP + _CARD_SEP_PX + count * self._card_row_height()

    def _card_message_height(self, lines: int | None = None) -> int:
        """C 区高 = 上下各一档分区间距 + 1px 分隔线 + 消息行高（1..2 行）。"""
        count = self._card_message_lines() if lines is None else lines
        return (2 * _CARD_SECTION_GAP + _CARD_SEP_PX
                + count * self._metrics_for("aux").lineSpacing())

    def _card_actions_height(self) -> int:
        """D 区高 = 一档分区间距 + 按钮高。"""
        return _CARD_SECTION_GAP + _CARD_BUTTON_H

    def _card_height(self) -> int:
        """卡片区高度（**声明值**）= 上边距 + 各区实测高 + 下边距。

        每一项都是度量出来的（行高 = 字体行高、消息高 = 实测行数 × 行距、按钮高
        = 钉死的 28px），所以"高度按内容实测"是可复算的账；测试用
        ``_card_box.layout().sizeHint().height()`` 与它逐像素对账（布局的固定高就是
        按这里的数设的，两者不等就是账目错了）。
        """
        rows = self._card_metric_rows()
        total = (_CARD_MARGIN_TOP + self._card_overview_height()
                 + self._card_metrics_height(rows) + self._card_message_height()
                 + self._card_actions_height() + _CARD_MARGIN_BOTTOM)
        return total

    def _sync_card_heights(self, rows=None) -> None:
        """把各区/各行固定高按账目落下去（唯一让"账目 = 实际布局"成立的地方）。"""
        rows = self._card_metric_rows() if rows is None else rows
        metrics = self._metrics_for("aux")
        lines = self._card_message_lines()
        self._card_overview.setFixedHeight(self._card_overview_height())
        self._card_balance_label.setFixedHeight(self._metrics_for("primary").height())
        self._card_tier_label.setFixedHeight(metrics.height())
        self._card_status_row.setFixedHeight(max(metrics.height(), int(_STATUS_DOT_PX)))
        self._card_metrics.setFixedHeight(max(1, self._card_metrics_height(rows)))
        self._card_metrics.setVisible(bool(rows))
        self._card_message_box.setFixedHeight(self._card_message_height(lines))
        self._card_message_label.setFixedHeight(lines * metrics.lineSpacing())
        self._card_actions.setFixedHeight(self._card_actions_height())

    def _card_row_baseline(self) -> int:
        """B 区行的共享基线（相对行顶）= 内边距 + 值列（13px）的 ascent。"""
        return _CARD_ROW_PAD_Y + self._metrics_for("value").ascent()

    def _card_row_offset(self, layer: str) -> int:
        """某一列的文字下移量 = ``基线 − 该层 ascent``：三列因此**同底**。

        QLabel 默认按行盒垂直居中，12/13/11px 三档字号的底线会各差 1px 上下；
        这里按基线定位（``AlignTop`` + 顶部内边距）而不是逐列居中——与胶囊
        "所有字号层共用一条基线"同一条纪律（E7 审计第 8 项的口径）。
        """
        return max(0, self._card_row_baseline() - self._metrics_for(layer).ascent())

    def _card_row_height(self) -> int:
        """B 区行高 = max(各列 offset + 该层行高) + 一个内边距（纯实测，不写死）。"""
        bottom = 0
        for layer in ("name", "value", "aux"):
            metrics = self._metrics_for(layer)
            bottom = max(bottom, self._card_row_offset(layer) + metrics.height())
        return bottom + _CARD_ROW_PAD_Y

    def _card_content_width(self) -> int:
        """卡片内容区宽度（左右各去掉一档内容边距）。"""
        return max(1, self._card_width() - 2 * _CARD_MARGIN_X)

    def _card_name_col_width(self, rows) -> int:
        """B 区段名列宽 = 各段名实测的较宽者（上限 ``_CARD_NAME_COL_MAX``）。"""
        metrics = self._metrics_for("name")
        return min(_CARD_NAME_COL_MAX,
                   max(int(metrics.horizontalAdvance(row.name)) for row in rows))

    def _card_value_col_width(self, rows, *, limit: int) -> int:
        """B 区值列宽 = 各段**声明样本**实测的较宽者（上限 ``limit``）。

        样本是段自己声明的最坏读数（``_InfoSeg.box_text``），当前读数一个都不进
        ——与胶囊的字段盒同一条纪律："9% → 100%" 改不动任何几何。
        """
        metrics = self._metrics_for("value")
        widest = max(int(metrics.horizontalAdvance(row.value_sample)) for row in rows)
        return max(0, min(widest, limit))

    def _card_detail_col_sample_width(self, rows) -> int:
        """B 区详情列宽样本（单位全称 · 来源 · 固定的更新时间样本）实测宽。"""
        metrics = self._metrics_for("aux")
        return max(int(metrics.horizontalAdvance(row.detail_sample)) for row in rows)

    def _card_width(self) -> int:
        """卡片宽 = max(胶囊自然宽, 最宽指标行 + 32)，钳进 [``_CARD_WIDTH``, 420]。

        三列全部由**声明样本**算出（段声明的 box_text / 静态来源串 / 固定的
        "00 秒前"），读数一个都不进——采样值永远改不动卡片几何，卡片宽度因此与
        胶囊的宽度白名单同一纪律。衡量不到（一行都没有）时回到 340 下限。
        """
        rows = self._card_metric_rows()
        if not rows:
            return max(self._capsule_width(), _CARD_WIDTH)
        needed = (2 * _CARD_MARGIN_X
                  + self._card_name_col_width(rows) + _CARD_COL_GAP
                  + self._card_value_col_width(rows, limit=1 << 20) + _CARD_COL_GAP
                  + self._card_detail_col_sample_width(rows))
        return max(self._capsule_width(),
                   max(_CARD_WIDTH, min(_CARD_WIDTH_MAX, int(needed))))

    def _card_column_widths(self, rows) -> tuple[int, int, int]:
        """B 区三列的实际宽度（(名, 值, 详情)）——三者和**恰好等于内容宽**。

        分配顺序：段名列 → 值列（先要它声明的宽——它是胶囊同款定宽字段盒，
        截断一个读数是最不该发生的坏，但给详情列留够 ``_CARD_DETAIL_MIN_PX``）
        → 详情列拿剩下的。为什么值列优先：读数的完整不可牺牲，详情（单位·来源·
        时间）可以右省略。两列的现文案都按各自列宽右省略，所以行宽恒等于内容宽
        ——"内容画出轮廓"在这条路径上不可能发生。
        """
        content = self._card_content_width()
        if not rows:
            return 0, 0, content
        name_w = self._card_name_col_width(rows)
        head = name_w + 2 * _CARD_COL_GAP
        # 值列先拿它声明的宽度（它是胶囊同款的"定宽字段盒"，截断一个读数是最不该
        # 发生的坏），但给详情列留够 _CARD_DETAIL_MIN_PX
        limit = max(1, content - head - _CARD_DETAIL_MIN_PX)
        value_w = self._card_value_col_width(rows, limit=limit)
        return name_w, value_w, max(0, content - head - value_w)

    # ------------------------------------------------------------ 卡片 B 区数据
    def _card_metric_rows(self) -> list[_CardMetricRow]:
        """B 区的行数据（**未收缩的全量段**：胶囊折进 +N 的段卡片照列全）。

        数据源与胶囊段同一处（``_compose_info_segments`` 的指标段），所以"值"必然
        是同一个合并串；单位全称/来源/更新时间是卡片独有的上下文（k3 §5.3：这些
        信息只在卡片，不进胶囊）。已知但不可用的段（``_unavailable_segments``）
        按配置序插在它本该在的位置——胶囊里没有它，卡片里有它的原因行。
        """
        segments = {seg.seg_id: seg for seg in self._compose_info_segments()
                    if seg.kind == island_layout.KIND_METRIC}
        unavailable = self._unavailable_segments()
        # 折叠态问一次就够（``_row_plan`` 每次都要过一遍输入白名单，逐段问会白烧
        # 三次字体指纹 + 屏查询）
        folded = self._row_plan().folded
        rows: list[_CardMetricRow] = []
        for metric_id in self._metric_ids():
            if metric_id in unavailable:
                rows.append(self._unavailable_row(metric_id, unavailable[metric_id]))
            elif metric_id in segments:
                rows.append(self._card_row_for_segment(segments[metric_id], folded))
        return rows

    @staticmethod
    def _card_row_name(seg) -> str:
        """段名：内置段查人话名字表，其余沿用胶囊的口径（label 或 id 前 6 字符）。"""
        return _CARD_METRIC_NAMES.get(seg.seg_id) or seg.label or \
            seg.seg_id[:island_segments.LABEL_MAX_CHARS]

    def _card_row_for_segment(self, seg, folded: tuple = ()) -> _CardMetricRow:
        """一个指标段 → 一行卡片数据。

        "当前值合并串" = ``段文案 + 单位``：E4 把合并串拆成"数值盒 + 单位盒"供胶囊
        排版，但格式化器的合并串仍是唯一真值（``split_value_unit`` 只在以单位结尾时
        拆分，拼回来逐字相同）——卡片不再自己格式化一遍，免得两处漂移。

        GPU 的上下文比设备槽多几样（量纲是"最忙引擎"、来源口径是 PDH 计数器路径、
        还有显卡名与引擎），它们**全部取自这一段的快照**：
        ``sample.sampled_at``（不是 GUI 收到它的时刻、更不是 CPU 的时间）、
        ``detail`` 里的 ``unit_full`` / ``source`` / ``detail`` / ``names_error``。
        读数器是这些字符串的唯一来源，卡片不自己拼第二份。
        """
        ok = seg.state == island_segments.STATE_OK
        value = seg.text + (seg.unit if ok else "")
        value_sample = seg.box_text + (seg.unit if seg.unit else "")
        source = ""
        updated = ""
        unit_full = _CARD_UNIT_FULL.get(seg.seg_id, "")
        hint = ""
        # 第三方段（声明清单 / 运行期注册）在场时，卡片上下文全部来自组件自己的
        # 声明 + 这一拍的快照：单位全称 / 来源口径 / 采样时刻（monotonic）。
        ext = self._extension_declaration(seg.seg_id)
        if ext is not None:
            ext_detail = str(getattr(ext, "detail", "") or "")
            source = str(getattr(ext, "source", "") or "")
            unit_full = str(getattr(ext, "unit_full", "") or "") or unit_full
            sample = self._ext_samples.get(seg.seg_id)
            sampled_at = None if sample is None else getattr(sample, "sampled_at", None)
        elif seg.seg_id == island_segments.NETWORK:
            source = _CARD_SOURCE_NET
            sampled_at = self._net_sample.sampled_at if self._net_sample is not None else None
        elif seg.seg_id in _SYS_METRIC_IDS:
            source = _CARD_SOURCE_SYS
            sampled_at = self._sys_sample.sampled_at if self._sys_sample is not None else None
        elif seg.seg_id == island_segments.GPU:
            source = _CARD_SOURCE_SYS
            sample = self._gpu_sample
            sampled_at = None if sample is None else sample.sampled_at
            unit_full = str(sample.detail.get("unit_full") or "") if sample is not None \
                else gpu_monitor.UNIT_FULL
        elif seg.seg_id == island_segments.FPS:
            # 帧率行的上下文全部来自 `island_fps` 的快照与呈现口径（目标进程 / Present
            # 来源 / 可选上屏帧率只在卡片说，胶囊上永远只有一个 4 位数字）。
            source = _CARD_SOURCE_SYS
            sampled_at = self._fps.sampled_at
            unit_full = island_fps.unit_full(self._fps.sample)
        else:
            sampled_at = None
        note = ""
        if seg.state == island_segments.STATE_OK:
            updated = self._card_updated_text(sampled_at)
            if seg.seg_id in folded:
                # 只折一部分时共享快照仍在顺带更新它（兄弟段还订阅着）——
                # 值是活的要如实显示，但「已被胶囊折叠」这件事也必须标注
                # （R1 评审修复：修复前这种行看起来与正常采样行毫无区别）。
                updated = f"已折叠 · {updated}"
        elif seg.state == island_segments.STATE_SAMPLING:
            # 冷启动/刚恢复订阅的首轮：胶囊上是 ``--``，卡片在来源行注「采样中」
            # （k3 §6.3 的四态口径），不写成"不可用"——它不是不会来，是还没来。
            # 例外：被胶囊折进 +N 的段根本没在采样（性能契约），卡片是唯一还看得见
            # 它的地方，如实说"暂停"而不是拿"采样中"糊弄（采样闸门语义不动）。
            updated = ("已折叠 · 暂停采样" if seg.seg_id in folded else "采样中")
        elif seg.state == island_segments.STATE_ERROR:
            note = f"{_CARD_UNAVAILABLE_NOTE}：{self._card_sample_error_reason(seg.seg_id)}"
        elif seg.state == island_segments.STATE_UNSUPPORTED and ext is not None:
            # 组件在采样器眼里"暂缺"（清单说在、文件已经不在这一类的竞态）：
            # 如实写原因，不冒充"未注册的段 id"。
            note = (f"{_CARD_UNAVAILABLE_NOTE}："
                    f"{self._card_sample_error_reason(seg.seg_id)}")
        elif seg.seg_id == island_segments.FPS:
            # 帧率段的 ``unknown`` 是"当前没有可采集的目标"（没开游戏 / 桌宠在前台 /
            # 目标退出了），**不是**配置里写了个不存在的段 id：原因句直接进详情列
            # （四态里只有它和 error 会走到这里，error 已经被上面那一支接走）。
            note = self._fps.reason() or f"{_CARD_UNAVAILABLE_NOTE}：{_CARD_UNKNOWN_REASON}"
        else:
            # 未注册的段 id（配置残留）：胶囊按 ``--`` 降级段占位，卡片说清原因
            note = f"{_CARD_UNAVAILABLE_NOTE}：{_CARD_UNKNOWN_REASON}"
        if ext is not None:
            # 第三方行的完整上下文（详情列放不下时从 tooltip / 无障碍描述读）：
            # 组件自己声明的一句话补充 + 来源 + 单位全称 + 这一拍的时刻，全部来自
            # 声明与快照，宿主不替它编第二份。
            hint = _card_hint_text(
                unit_full, ext_detail, source, note,
                f"采样于 {updated}" if seg.state == island_segments.STATE_OK else "")
        elif seg.seg_id == island_segments.GPU:
            hint = self._gpu_card_hint(seg, note=note, updated=updated)
        elif seg.seg_id == island_segments.FPS:
            hint = self._fps_card_hint(seg, note=note, updated=updated)
        return _CardMetricRow(
            seg_id=seg.seg_id, name=self._card_row_name(seg), value=value,
            value_sample=value_sample,
            unit_full=unit_full, source=source,
            updated=updated, state=seg.state, severity=seg.severity, note=note,
            hint=hint)

    def _gpu_card_hint(self, seg, *, note: str, updated: str) -> str:
        """GPU 行的完整上下文（详情列放不下时从 tooltip / 无障碍描述读）。

        内容全部来自这一段的快照：量纲（最忙引擎而非总算力）、显卡名与引擎
        （``adapter_label``：名字只从 DXGI 来，拿不到就如实显示真实 LUID）、
        PDH 来源路径、名字枚举失败的原因（``names_error``，读数照常显示但原因不丢）、
        这一拍的采样时刻。段名与值不重复进来（它们在行上已经可见）。
        """
        sample = self._gpu_sample
        if sample is None:
            # 还没有快照（采样中 / 只知道一个保持下来的故障原因）：能说的只有口径。
            return _card_hint_text(gpu_monitor.UNIT_FULL,
                                   note or gpu_monitor.SOURCE_TEXT)
        detail = sample.detail if isinstance(sample.detail, dict) else {}
        return _card_hint_text(
            detail.get("unit_full") or gpu_monitor.UNIT_FULL,
            detail.get("detail") or gpu_monitor.adapter_label(sample.adapter),
            detail.get("names_error"),
            detail.get("source") or gpu_monitor.SOURCE_TEXT,
            note,
            f"采样于 {updated}" if seg.state == island_segments.STATE_OK else "",
        )

    def _fps_card_hint(self, seg, *, note: str, updated: str) -> str:
        """帧率行的完整上下文（详情列放不下时从 tooltip / 无障碍描述读）。

        文案**不在岛侧拼**：口径全称 / 目标进程 / 目标选定方式 / 可选上屏帧率（另一
        口径）/ 溢出说明 / 后端 stderr 原文 / Present 采集来源 / 采样时刻，全部由
        ``island_fps.hint_parts`` 从这一段的快照给出——多宠共用一个采集器，口径与
        拒收口径必须同源。段名与值不重复进来（行上已可见）。
        """
        return _card_hint_text(*self._fps.hint_parts(
            reason=note or self._fps.reason(), updated=updated,
            ok=seg.state == island_segments.STATE_OK))

    def _card_sample_error_reason(self, metric_id: str) -> str:
        """采样失败的原因：这一段的快照自带 ``error_reason``，没有就如实说"采样失败"。"""
        if metric_id in self._ext_samples:
            reason = str(getattr(self._ext_samples[metric_id], "error_reason", "") or "").strip()
            if reason:
                return reason
        if self._ext_error_hold.get(metric_id, "").strip():
            return self._ext_error_hold[metric_id].strip()
        if island_segments.get_segment(metric_id) is not None \
                or metric_id in self._ext_manifest:
            # 第三方段：没有快照也没有保持的故障 = 采样器这一拍还没说话
            return "组件采样失败"
        if metric_id == island_segments.FPS:
            # 帧率段的原因在快照里（权限不足/异常退出的原文）或折叠前的保持表里；
            # "折一下就没了"是静默吞故障，所以这条路径必须能读到它。
            reason = self._fps.reason()
            if reason:
                return reason
        if metric_id == island_segments.GPU and self._gpu_sample is not None:
            reason = str(getattr(self._gpu_sample, "error_reason", "") or "").strip()
            if reason:
                return reason
        elif metric_id in _SYS_METRIC_IDS and self._sys_sample is not None:
            reason = str(getattr(self._sys_sample, "error_reason", "") or "").strip()
            if reason:
                return reason
        for hold in (self._gpu_error_hold, self._sys_error_hold):
            reason = hold.get(metric_id, "").strip()
            if reason:
                return reason
        return "采样失败"

    def _unavailable_row(self, metric_id: str, reason: str) -> _CardMetricRow:
        """已知但不可用的一段 → 卡片原因行（胶囊里它一个像素都不占）。"""
        note = f"{_CARD_UNAVAILABLE_NOTE}：{reason}"
        return _CardMetricRow(
            seg_id=metric_id,
            name=_CARD_METRIC_NAMES.get(metric_id)
            or metric_id[:island_segments.LABEL_MAX_CHARS],
            value=island_segments.UNKNOWN_TEXT,
            value_sample=island_segments.UNKNOWN_TEXT,
            state=island_segments.STATE_UNSUPPORTED,
            note=note,
            # 原因句本身就长（"该指标不可用：…"），详情列一省就只剩半句——
            # 完整原因同样挂到 tooltip / 无障碍描述上。
            hint=note)

    @staticmethod
    def _card_updated_text(sampled_at) -> str:
        """更新时间（"3 秒前"）：读数什么时候采的，卡片说清。"""
        if sampled_at is None:
            return "未采样"
        elapsed = max(0.0, time.monotonic() - float(sampled_at))
        if elapsed < 1.0:
            return "刚刚"
        if elapsed < 60.0:
            return f"{int(elapsed)} 秒前"
        if elapsed < 3600.0:
            return f"{int(elapsed // 60)} 分钟前"
        return f"{int(elapsed // 3600)} 小时前"

    def _card_value_color(self, row, primary_color: QColor, secondary_color: QColor) -> QColor:
        """B 区值列颜色：ok 态跟随告警色（与胶囊同一套），非 ok 态一律次色不染色。

        非 ok 态不染红是 E2 的口径（胶囊上段的错误保持安静），卡片上段的错误靠
        详情列的整句原因说话——红色在这个产品里只属于状态点与告警数值。
        """
        if row.state != island_segments.STATE_OK:
            return QColor(secondary_color)
        alert = self._alert_color(row.severity)
        return QColor(alert) if alert is not None else QColor(primary_color)

    def _card_row_signature(self, rows) -> tuple:
        """B 区行控件的重建判据：行的身份/状态 + 字号 + 卡片宽（**文字不进**）。"""
        return (tuple((row.seg_id, row.state, row.note) for row in rows),
                self._fonts_key(), self._card_width())

    def _card_detail_display(self, row, detail_w: int) -> str:
        """详情列的现文案：整句放不下就先省单位全称，再放不下才右省略。"""
        detail_fm = self._metrics_for("aux")
        if detail_fm.horizontalAdvance(row.detail) <= detail_w:
            return row.detail
        return island_layout.elide_to_width(row.detail_short, detail_w, detail_fm)

    def _sync_card_rows(self, rows, primary_color: QColor, secondary_color: QColor) -> None:
        """B 区行：签名变了才重建控件，其余只改三列文字（每秒 tick 不重建）。"""
        if self._card_row_signature(rows) != self._card_rows_key:
            self._rebuild_card_rows(rows)
        name_w, value_w, detail_w = self._card_column_widths(rows)
        detail_color = self._unit_color(secondary_color)
        for row, (name_label, value_label, detail_label) in zip(
                rows, self._card_row_labels):
            name_label.setText(island_layout.elide_to_width(
                row.name, name_w, self._metrics_for("name")))
            self._apply_card_style(name_label, f"color: {secondary_color.name()};")
            value_label.setText(island_layout.elide_to_width(
                row.value, value_w, self._metrics_for("value")))
            self._apply_card_style(value_label, "color: {};".format(
                self._card_value_color(row, primary_color, secondary_color).name()))
            detail_label.setText(self._card_detail_display(row, detail_w))
            self._apply_card_style(detail_label, f"color: {detail_color.name()};")
            # 详情列是**右省略**的排版结果，被省掉的部分（长显卡名、PDH 来源口径、
            # 名字枚举失败的原因）仍须可读：值/详情两列挂同一份完整上下文，
            # tooltip 给鼠标用户、accessibleDescription 给读屏（同值不同道）。
            for label in (value_label, detail_label):
                if label.toolTip() != row.hint:
                    label.setToolTip(row.hint)
                if label.accessibleDescription() != row.hint:
                    label.setAccessibleDescription(row.hint)

    def _rebuild_card_rows(self, rows) -> None:
        """重建 B 区行控件：三列宽钉死（和 = 内容宽），文字按各自列宽右省略。

        列宽钉死是"内容不画出轮廓"的机器保证：任何超宽读数在进 QLabel 之前就已
        按列宽省略，行宽恒等于内容宽。
        """
        for widget in self._card_row_widgets:
            widget.setParent(None)
            widget.deleteLater()
        self._card_row_widgets = []
        self._card_row_labels = []
        name_w, value_w, detail_w = self._card_column_widths(rows)
        row_h = self._card_row_height()
        for _row in rows:
            widget = QWidget(self._card_metrics)
            widget.setFixedHeight(row_h)
            line = QHBoxLayout(widget)
            line.setContentsMargins(0, 0, 0, 0)
            line.setSpacing(_CARD_COL_GAP)
            labels = []
            for layer, width in (("name", name_w), ("value", value_w),
                                 ("aux", detail_w)):
                label = QLabel(widget)
                label.setFont(self._font_for(layer))
                label.setFixedWidth(width)
                label.setAlignment(Qt.AlignmentFlag.AlignLeft
                                   | Qt.AlignmentFlag.AlignTop)
                label.setContentsMargins(0, self._card_row_offset(layer), 0, 0)
                line.addWidget(label)
                labels.append(label)
            self._card_metrics_layout.addWidget(widget)
            self._card_row_widgets.append(widget)
            self._card_row_labels.append((labels[0], labels[1], labels[2]))
        self._card_rows_key = self._card_row_signature(rows)

    def _resize_card_if_needed(self) -> None:
        """卡片内容高度变化后重新锁定窗口尺寸——**只在几何动画静止时**。

        展开动画进行中重设固定尺寸会把 0.18s 的滑移整段吃掉（动画还没走完窗口
        就跳到终点），所以判据带 ``_geo_to``。
        """
        if self._mode == "expanded" and self._geo_to is None:
            self._apply_fixed_size()

    def _on_card_tick(self) -> None:
        """卡片开着时的每秒 tick：刷新更新时间/状态文字，并让高度跟上内容。

        停表纪律同完成瞬态：收起与隐藏都停表，静止时零定时器开销。
        """
        self._sync_card_labels()
        self._resize_card_if_needed()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if hasattr(self, "_card_box"):
            self._card_box.setGeometry(
                0, _CAPSULE_HEIGHT, self.width(),
                max(0, self.height() - _CAPSULE_HEIGHT))

    def _on_card_toggle(self) -> None:
        self.toggle_pet_requested.emit()
        self.collapse_card()

    def _on_card_chat(self) -> None:
        self.open_chat_requested.emit()
        self.collapse_card()

    def _on_card_settings(self) -> None:
        self.open_settings_requested.emit()
        self.collapse_card()

    def expand_card(self) -> None:
        """展开卡片（单击胶囊的默认行为）。停靠状态先滑出再展开。"""
        if self._mode == "expanded":
            self.collapse_card()
            return
        self._dock_back_timer.stop()
        self._mode = "expanded"
        self._hover_peek = False
        # 单击展开时鼠标多半已在岛内（enterEvent 已把悬停放大目标置 1.02），
        # 展开态禁用整窗缩放，目标拉回 1.0（见 paintEvent 截边说明）
        self._hover_scale_target = 1.0
        self._card_box.show()
        self._sync_card_labels()
        self._animate_to(self._target_rect())
        # hover 暂停的第一现场（6.1sol 终审：「先进入胶囊、再单击展开」是最常见
        # 路径——enterEvent 早在 normal 态就发生过了，展开后鼠标没动不会再来一次；
        # 不看 hover 状态就会在用户正读着卡片时被 15s 表收掉）。用自记的
        # ``_cursor_inside`` 而不是 underMouse()：后者由窗口系统置位，语义相同但
        # 在合成事件下不可验（自记旗帜走 enter/leave 两个 handler，路径唯一）。
        if not self._cursor_inside:
            self._card_collapse_timer.start()
        # 卡片开着时每秒刷一次"更新时间"与状态文字（收起/隐藏即停表，同完成瞬态）
        self._card_tick_timer.start()
        self._sync_content_sampling()
        # 展开卡片即停声纳环（卡片态是"读者在别处"的形态，见 _sonar_eligible）
        self._sync_sonar_ring()
        self.card_expanded.emit()

    def collapse_card(self, *, animate: bool = True) -> None:
        """收起卡片；配置为停靠边时收回细条。拖拽起手时 animate=False
        （动画会与拖拽抢几何，直接把窗拽回停靠位）。"""
        if self._mode != "expanded":
            return
        self._card_collapse_timer.stop()
        self._card_tick_timer.stop()
        self._card_box.hide()
        self._mode = "docked" if self.dock_edge != "none" else "normal"
        self._hover_peek = False
        if animate:
            self._animate_to(self._target_rect())
        else:
            self._geo_from = None
            self._geo_to = None
            self._set_free_geometry(self._target_rect())
            self._apply_fixed_size()
        self._sync_content_sampling()    # 收进细条 = 内容全不画 → 全停采样
        self._sync_sonar_ring()          # 收到 normal 才可能重新播（见 _sonar_eligible）

    # ------------------------------------------------------------ 动效核心
    def _bounce(self, impulse: float = 4.0) -> None:
        """事件弹跳：下压几像素再弹簧回位（整数位移，文字不糊）+ 表面轻压。"""
        self._kick_y = 1.5 + min(float(impulse), 5.0)
        self._kick_vy = 0.0
        self._squish_v -= impulse * 0.14
        self._scale_v += impulse * 0.06
        self._ensure_anim_timer()

    def _ensure_anim_timer(self) -> None:
        # 隐藏即休眠：隐藏态不被后台事件（AI 回复/余额刷新）拉起 60fps
        # 动画定时器空转；showEvent 恢复时会重刷新并重启动效
        if not self.isVisible():
            return
        if not self._anim_timer.isActive():
            self._anim_last = time.monotonic()
            self._anim_timer.start()

    def _animating(self) -> bool:
        if self._geo_to is not None:
            return True
        if self._mode == "expanded":
            # 展开卡片态：paintEvent 把 kick/呼吸平移、tilt/scale 整窗变换全丢
            # （贴边底板被位移或 >1 缩放硬切的实机修复，见 paintEvent），
            # ``_squished_capsule_rect`` 又冻结 squish——上面这些通道收敛到任何
            # 值都不改变一个像素，不能当"动画活跃"判据。否则每次 bump（岛被宠
            # 撞）/余额弹跳都在半透明顶层窗上白跑 ~1s 的 60fps 重绘（实机每帧
            # 一次窗口合成）。几何动画（_geo_to，展开/收起滑移）照常驱动重绘。
            return False
        # 阈值 = 感知量级（44px 高胶囊）：|squish-1| 0.01 → 0.44px 形变、
        # |squish_v| 0.06 → 0.2px 振速、|tilt_v| 3.0 → 0.27°（约 0.6px 边缘位移）。
        # 低于它仍是"动画"的话，每次撞击都要把半透明顶层窗多画几十帧不可见的
        # 尾巴（实测单次撞击的静音耗时 0.88s → 0.61s，见同目录探针记录）。
        return (
            abs(self._kick_x) > 0.3 or abs(self._kick_y) > 0.3
            or abs(self._kick_vx) > 5.0 or abs(self._kick_vy) > 5.0
            or abs(self._squish - 1.0) > 0.01 or abs(self._squish_v) > 0.06
            or abs(self._scale - self._hover_scale_target) > 0.001
            or abs(self._scale_v) > 0.005
            or abs(self._tilt) > 0.05
            or abs(self._tilt_v) > 3.0
            or time.monotonic() < self._breathe_until
        )

    def _settle_bump_animation(self) -> None:
        """把 bump 动效通道（踢动/形变/倾斜）连同速度一起归零。

        只碰 bump 自己驱动的通道：``_scale``/``_scale_v`` 归事件弹跳
        （``_bounce``）与悬停放大，不在撞击动效的收尾范围内（否则会硬切悬停
        放大 / 事件弹跳的中间帧）。
        """
        self._kick_x = self._kick_y = 0.0
        self._kick_vx = self._kick_vy = 0.0
        self._squish, self._squish_v = 1.0, 0.0
        self._tilt, self._tilt_v = 0.0, 0.0

    def _on_anim_tick(self) -> None:
        now = time.monotonic()
        dt = now - (self._anim_last or now)
        self._anim_last = now

        # bump 动效的动画时间上限（按动画时间累计，停表期间的墙钟不计入）：
        # 连撞把弹簧顶在钳位上时阈值判据可能长期为真，到点强制收尾归零。
        if self._bump_anim_left > 0.0:
            self._bump_anim_left = max(0.0, self._bump_anim_left - dt)
            if self._bump_anim_left == 0.0:
                self._settle_bump_animation()

        # 几何滑移（停靠/展开）：定时插值，ease-out
        if self._geo_to is not None and self._geo_from is not None:
            self._geo_t = min(1.0, self._geo_t + dt / 0.18)
            k = ease_out_cubic(self._geo_t)
            rect = QRect(
                round(self._geo_from.x() + (self._geo_to.x() - self._geo_from.x()) * k),
                round(self._geo_from.y() + (self._geo_to.y() - self._geo_from.y()) * k),
                round(self._geo_from.width() + (self._geo_to.width() - self._geo_from.width()) * k),
                round(self._geo_from.height() + (self._geo_to.height() - self._geo_from.height()) * k),
            )
            self._set_free_geometry(rect)
            if self._geo_t >= 1.0:
                self._geo_from = None
                self._geo_to = None
                self._set_free_geometry(self._target_rect())

        # 位移踢动回位 / 微缩放 / 受击倾斜 / 果冻形变回弹
        self._kick_x, self._kick_vx = spring_step(
            self._kick_x, self._kick_vx, 0.0, dt)
        self._kick_y, self._kick_vy = spring_step(
            self._kick_y, self._kick_vy, 0.0, dt)
        self._scale, self._scale_v = spring_step(
            self._scale, self._scale_v, self._hover_scale_target, dt,
            stiffness=_SCALE_STIFFNESS, damping=_SCALE_DAMPING)
        self._scale = max(_SCALE_MIN, min(_SCALE_MAX, self._scale))
        self._tilt, self._tilt_v = spring_step(
            self._tilt, self._tilt_v, 0.0, dt,
            stiffness=_TILT_STIFFNESS, damping=_TILT_DAMPING)
        # 倾斜状态钳位：paintEvent 早就把绘制钳到 ±_TILT_MAX_DEG，不钳状态时
        # 连撞共振能把振幅泵到十几度——弹簧在"画不出来"的幅度里来回，速度随之
        # 无界（实测同向连打 _tilt_v 109），_animating() 的阈值判据随之失效。
        # 与 ``_squish`` 的状态钳位同口径（区别只是那里钳在 _on_anim_tick 前段）。
        self._tilt = max(-_TILT_MAX_DEG, min(_TILT_MAX_DEG, self._tilt))
        self._squish, self._squish_v = spring_step(
            self._squish, self._squish_v, 1.0, dt,
            stiffness=_SQUISH_STIFFNESS, damping=_SQUISH_DAMPING)
        self._squish = max(_SQUISH_MIN, min(_SQUISH_MAX, self._squish))
        # 踢动位移钳制在窗口内（±5px）：弹簧过冲也不画出界被裁
        self._kick_x = max(-5.0, min(5.0, self._kick_x))
        self._kick_y = max(-5.0, min(5.0, self._kick_y))
        if not self._animating():
            # 停表即收尾：连速度一起归零（残留速度会让下一次动画起步时凭空
            # 抖一下，也是 _animating() 判据失效的温床）
            self._settle_bump_animation()
            self._scale = 1.0 if self._hover_scale_target == 1.0 else self._hover_scale_target
            self._bump_anim_left = 0.0
            self._anim_timer.stop()
        self.update()

    def _finish_animations(self) -> None:
        """立即跳到所有动画终点（测试与即需布局用）。"""
        self._anim_timer.stop()
        self._geo_from = None
        self._geo_to = None
        self._kick_x = self._kick_y = 0.0
        self._kick_vx = self._kick_vy = 0.0
        self._squish = 1.0
        self._squish_v = 0.0
        self._scale = 1.0
        self._scale_v = 0.0
        self._tilt = 0.0
        self._tilt_v = 0.0
        self._breathe_until = 0.0
        self._bump_anim_left = 0.0
        self._hover_scale_target = 1.0
        self._set_free_geometry(self._target_rect())
        self.update()

    def _animate_to(self, target: QRect) -> None:
        if target == self.geometry() and not self._anim_timer.isActive():
            self._set_free_geometry(target)
            return
        self._geo_from = self.geometry()
        self._geo_to = target
        self._geo_t = 0.0
        self._ensure_anim_timer()

    def _emit_geometry_changed(self) -> None:
        callback = self.on_geometry_changed
        if callable(callback):
            try:
                callback()
            except Exception:
                pass

    def _set_free_geometry(self, rect: QRect) -> None:
        """动画期间解除固定尺寸直接摆几何；到位后由 _apply_fixed_size 锁定。"""
        self.setMinimumSize(0, 0)
        self.setMaximumSize(16777215, 16777215)
        self.setGeometry(rect)
        self._emit_geometry_changed()

    # ------------------------------------------------------------ 内部
    def _visible_parts(self) -> tuple[bool, bool, bool, bool]:
        c = self._cfg
        icon = bool(c.get("show_icon", True))
        name = bool(c.get("show_name", True))
        info = bool(c.get("show_info", True))
        status = bool(c.get("show_status", True))
        if not (icon or name or info or status):
            info = True
        return icon, name, info, status

    def _info_text(self) -> str:
        """主信息段的文案（单串视图；绘制走 :meth:`_compose_info_segments`）。

        网络接管分支已在 P1 D1 删除：网络是普通指标段，信息槽永远由
        ``info_mode`` 决定（见 ``_primary_segment``）。
        """
        return self._primary_segment().text

    def _info_color(self, fallback: QColor) -> QColor:
        """余额峰谷主信息跟随设置的峰谷提示颜色开关。

        开启后高峰显示红色、低谷显示绿色；关闭或非余额峰谷模式使用传入的兜底色
        （主信息段用主文字色，见 ``_content_cache``）。
        """
        if str(self._cfg.get("info_mode") or "time") != "balance_tier":
            return fallback
        if not bool(self.config.get("balance_tier_color_enabled", True)):
            return fallback
        try:
            from . import balance as balance_mod

            tier = balance_mod.deepseek_pricing_tier()
        except Exception:
            return fallback
        if tier == "peak":
            return QColor("#e5484d")
        if tier == "idle":
            return QColor("#30a46c")
        return fallback

    # ------------------------------------------------------------ 段管线
    def _font_for(self, layer: str) -> QFont:
        """按字号层取内容字体（懒建 + 缓存；应用字体变化由 changeEvent 清缓存）。"""
        font = self._fonts.get(layer)
        if font is None:
            px, weight = _FONT_LAYERS[layer]
            font = QFont(self.font())
            font.setPixelSize(px)
            font.setWeight(weight)
            self._fonts[layer] = font
        return font

    def _metrics_for(self, layer: str) -> QFontMetrics:
        """按字号层取字体度量（缓存对象：QFontMetrics 构造有成本）。"""
        metrics = self._font_metrics.get(layer)
        if metrics is None:
            metrics = QFontMetrics(self._font_for(layer))
            self._font_metrics[layer] = metrics
        return metrics

    def _fonts_key(self) -> tuple:
        """字号各层的度量指纹（内容层缓存 key 的"字体"分量）。

        指纹探针（行度量 + 数字串宽度）由 ``island_segments`` 提供——系统字体/
        字号/DPI 一变就换键，盒宽与内容层位图都跟着重测。
        """
        return tuple(island_segments.font_metrics_key(self._metrics_for(layer))
                     for layer in _FONT_LAYERS)

    def _metric_ids(self) -> tuple[str, ...]:
        """``island_metrics`` 的段 id 序（**列表序 = 用户优先级 = 尾部先折叠**）。

        这里只做"非空字符串 + 去重"：合法 id 的归一化在 config 层（Slice C），
        未知 id 刻意透传——配置被手改或调用方给的是桩对象时，未知段要降级成
        ``--`` 段（见 ``_unknown_segment``），而不是让整条岛炸掉。
        """
        metrics = self.config.get("island_metrics", [])
        if not isinstance(metrics, (list, tuple)):
            return ()
        ids: list[str] = []
        for item in metrics:
            if not isinstance(item, str) or not item or item in ids:
                continue
            ids.append(item)
        return tuple(ids)

    @staticmethod
    def _state_text(state: str, value: str) -> str:
        """四态兜底：只有 ``ok`` 显示读数；其余一律同宽 ``--``（不伪造 0）。"""
        return value if state == island_segments.STATE_OK else island_segments.UNKNOWN_TEXT

    def _primary_segment(self) -> _InfoSeg:
        """首段（恒为段列表第一项）：time / custom / balance / balance_tier 主信息。

        按模式取测宽样本：时间为 ``88:88`` 定宽（数字不推邻居），余额/峰谷取真实
        最长单行形态，custom 用配置文本自身。现文案超过样本时按现文案加宽（见
        :func:`place_info_segments`），定宽靠样本而不是靠截断。

        **只有 custom 允许被收缩省略**（``elide_px``）：时间/余额/峰谷是定宽主信息，
        它们是产品契约的锚点，收缩不得动（见设计稿 §3.2 保底级）。
        """
        mode = str(self._cfg.get("info_mode") or "time")
        if mode == "custom":
            text = str(self._cfg.get("custom_text") or "").strip() or "自定义"
            sample = text
            elide_px = _PRIMARY_ELIDE_PX
        elif mode == "balance_tier":
            text = self._balance_tier_display_text()
            sample = _PRIMARY_SAMPLES["balance_tier"]
            elide_px = 0
        elif mode == "balance":
            text = self._balance_text
            sample = _PRIMARY_SAMPLES["balance"]
            elide_px = 0
        else:
            from PySide6.QtCore import QTime

            text = QTime.currentTime().toString("HH:mm")
            sample = _PRIMARY_SAMPLES["time"]
            elide_px = 0
        return _InfoSeg(kind=island_layout.KIND_PRIMARY, seg_id="primary", label="", text=text,
                        box_text=sample, state=island_segments.STATE_OK,
                        elide_px=elide_px)

    def _network_segment(self) -> _InfoSeg:
        """网络段：定宽槽串（``format_network_slot`` 的 5 列契约）直接进字段盒。

        没有 label（↓↑ 是槽串自带的 glyph），采样还没出第一轮时按四态显示 ``--``。
        盒宽**恒为声明样本**（「千兆满速 + 近秒级延迟」现实包络）：读数在盒内右对齐
        伸缩，采样值永不进宽度键——R1 评审修复：``7947a18`` 曾让盒宽取 max(样本,
        现文案)，结果采样值经 ``box_text`` 变相进键，临界预算下会形成「折叠→清样本
        →恢复→再折叠」的订阅振荡；包络外读数（G 级速率/四位毫秒）改由字段盒右省略
        护栏承接（``format_field_box`` 自带 warning），不破坏宽度不变量。
        """
        sample = self._net_sample
        if sample is None:
            text = island_segments.UNKNOWN_TEXT
            state = island_segments.STATE_SAMPLING
        else:
            text = net_monitor.format_network_slot(
                sample.down_bps, sample.up_bps, sample.latency_ms,
                lost=sample.latency_lost)
            state = island_segments.STATE_OK
        return _InfoSeg(kind=island_layout.KIND_METRIC, seg_id=island_segments.NETWORK, label="",
                        text=text, box_text=_NETWORK_BOX_SAMPLE, state=state)

    def _device_segment(self, metric_id: str) -> _InfoSeg:
        """CPU / 内存段：读数来自共享的 ``SysMonitor`` 快照（无快照 = sampling）。

        有快照但该字段是 None（CPU 差分首样本）也算 sampling——"还没读数"与
        "读数是 0"必须分得开（``--`` 不伪造 0）。刚被恢复（重新订阅）的段同样先报
        sampling：见 ``_sys_pending``。

        格式化器仍输出合并串（``38%``，卡片与其他消费方照用），这里用同一条
        「值/单位分离」路径把它拆成"数值盒的 38 + 单位盒的 %"。**单位取声明值**
        而不是拆分结果：拆分对兜底串 ``--`` 返回空单位，跟着读数走就会在采样态
        与 ok 态之间差一个单位盒宽（状态切换跳宽）；声明值进宽度白名单键，
        读数不进。

        ``severity``（告警级）不在这里算，而是**读** ``self._alert_severity``——那一拍
        的账在样本到达时（``_update_alert_severity``）就记好了。这样做有两个必要
        的效果：滞回不会因为"两次读数之间没绘制"而漏拍；被折进 +N 的段退订后显示
        退回 ``--``，但它的告警级照旧推进/保持，chip 告警点不会一闪即灭。
        """
        sample = self._sys_sample
        if sample is None or metric_id in self._sys_pending:
            if metric_id in self._sys_error_hold:
                # 被折前最后一拍是采样故障（``_sys_error_hold``，见
                # ``_sync_sys_slot`` 收口袋）：折叠不埋没故障——chip 告警点
                # 与卡片原因行靠这条保持，直到恢复订阅/新快照到达。
                state, value = island_segments.STATE_ERROR, None
            else:
                state, value = island_segments.STATE_SAMPLING, None
        elif sample.state != island_segments.STATE_OK:
            state, value = island_segments.STATE_ERROR, None
        else:
            value = (sample.cpu_percent if metric_id == island_segments.CPU
                     else sample.mem_percent)
            state = (island_segments.STATE_OK if value is not None
                     else island_segments.STATE_SAMPLING)
        formatter = (sys_monitor.format_cpu if metric_id == island_segments.CPU
                     else sys_monitor.format_mem)
        text, _unit = island_segments.split_value_unit(
            self._state_text(state, formatter(value)), island_segments.PERCENT_UNIT)
        return _InfoSeg(kind=island_layout.KIND_METRIC, seg_id=metric_id,
                        label=_DEVICE_LABELS[metric_id],
                        text=text,
                        box_text=_DEVICE_VALUE_SAMPLE, state=state,
                        unit=island_segments.PERCENT_UNIT,
                        severity=self._alert_severity.get(metric_id, ""))

    @staticmethod
    def _unknown_segment(metric_id: str) -> _InfoSeg:
        """未实现的段 id（配置残留）：降级成 unknown 态 ``--`` 段，不崩不留洞。"""
        return _InfoSeg(kind=island_layout.KIND_METRIC, seg_id=metric_id,
                        label=metric_id[:island_segments.LABEL_MAX_CHARS],
                        text=island_segments.UNKNOWN_TEXT,
                        box_text=island_segments.UNKNOWN_TEXT,
                        state=island_segments.STATE_UNKNOWN)

    def _gpu_segment(self) -> _InfoSeg:
        """GPU 段：读数来自底座 GPU 那一本账（无快照 = sampling）。

        与 ``_device_segment`` 逐条同款：只有 ``ok`` 才画数值，其余一律同宽 ``--``；
        值/单位拆两盒、单位取声明值（读数不进宽度键）。**格式化的唯一出口**是
        ``gpu_monitor.format_gpu``（读数器自己的口径：取整 + 无读数给 ``--``），
        卡片读的是同一个段对象，所以胶囊与卡片不可能各格式化一遍。

        ``text`` 里永远是裸百分比（如 ``"42"``），显卡名/引擎/量纲只进卡片详情——
        胶囊上多一个字的设备名都会把邻居推歪（与网络段的 glyph 口径同一纪律）。
        """
        sample = self._gpu_sample
        if sample is None:
            if island_segments.GPU in self._gpu_error_hold:
                # 被折前最后一拍是采样故障（``_gpu_error_hold``，见
                # ``_sync_sys_slot`` 收口袋）：折叠不埋没故障。
                state, percent = island_segments.STATE_ERROR, None
            else:
                state, percent = island_segments.STATE_SAMPLING, None
        else:
            state = sample.state
            percent = sample.percent if state == island_segments.STATE_OK else None
        text, _unit = island_segments.split_value_unit(
            self._state_text(state, gpu_monitor.format_gpu(percent)),
            island_segments.PERCENT_UNIT)
        return _InfoSeg(kind=island_layout.KIND_METRIC, seg_id=island_segments.GPU,
                        label=_GPU_LABEL, text=text,
                        box_text=_GPU_VALUE_SAMPLE, state=state,
                        unit=island_segments.PERCENT_UNIT,
                        severity=self._alert_severity.get(island_segments.GPU, ""))

    def _fps_segment(self) -> _InfoSeg:
        """游戏帧率段：读数来自 ``pet/island_fps.py`` 的段状态机（无快照 = sampling）。

        与 GPU 逐条同款，但**段对象与四态判定都不在这里**：``FpsSlot.segment`` 是唯一
        出口（它同时管"格式化出口唯一"与"盒宽样本恒为 4 位"）。这里只做转发，岛侧不
        重新解释读数——多宠共用一个采集器，口径必须集中在一处。

        段里没有 ``unit``：帧率的单位就是段标签 FPS 本身，多画一个单位盒只会白占
        ``_unit_box_width`` 那一档宽度（同网络段不画单位的口径）。
        """
        return self._fps.segment()

    def _gpu_unavailable_reason(self) -> str:
        """GPU 当前**不可用**的原因（``""`` = 可用）。

        只有读数器明确报 ``unsupported``（如平台没有 PDH）才算"这台机器没有这个
        能力"——那一段按既有 E5 机制**不占位**、卡片留一行原因。``sampling`` /
        ``unknown`` / ``error`` 都不是"没有能力"：段照占位（``--``）并在卡片里说
        明这一拍为什么没有读数，把"当前没实例"说成"没 GPU"才是谎话。
        """
        sample = self._gpu_sample
        if sample is None or sample.state != island_segments.STATE_UNSUPPORTED:
            return ""
        return (str(getattr(sample, "error_reason", "") or "").strip()
                or "GPU 采集在当前平台不可用")

    def _unavailable_segments(self) -> dict[str, str]:
        """**已知但当前不可用**的段 id → 原因（P2 Slice E5 的机制 + GPU 的动态判定）。

        两个来源：① 宿主填写的能力表 ``_unavailable_reasons``（新能力占位往那里塞
        条目即可）；② GPU 的当刻读数（见 :meth:`_gpu_unavailable_reason`）——它是
        **运行期**能力，不能写死成静态条目。

        口径是**已知 id 才走这条**：这类段胶囊里连占位都不画（不是 ``--``，是
        "不存在"），只在卡片 B 区留一行「该指标不可用 + 原因」。未知 id 依旧降级成
        胶囊 ``--`` 段（见 :meth:`_unknown_segment`），既有行为一个像素都不动。
        """
        known = self._metric_ids()   # 提在推导式外：表里每多一项就少读一次配置
        reasons = {key: str(value) for key, value in self._unavailable_reasons.items()
                   if key in known}
        if island_segments.GPU in known:
            reason = self._gpu_unavailable_reason()
            if reason:
                reasons[island_segments.GPU] = reason
        if island_segments.FPS in known:
            # 帧率的"没有这个能力"有两处来源（平台闸门 / 内置采集程序不在），都在
            # ``island_fps`` 里判完给一句话——岛侧不重判，免得两处口径漂移。
            reason = self._fps.unavailable_reason()
            if reason:
                reasons[island_segments.FPS] = reason
        if island_segments.FPS in known:
            reason = self._fps.unavailable_reason()
            if reason:
                reasons[island_segments.FPS] = reason
        # 第三方组件**暂缺**（清单声明了、provider 文件不在）：保留用户偏好、不执行
        # ——胶囊连占位都不画（它不是段列表成员），卡片写清"哪一份文件不在"。
        # 判据取岛自己缓存的那份清单（不在这里重新扫目录：本方法是绘制路径上的）。
        for metric_id in known:
            if metric_id in island_segments.BUILTIN_SEGMENT_IDS or metric_id in reasons:
                continue
            declaration = self._ext_manifest.get(metric_id)
            if declaration is not None and not getattr(declaration, "provider_present", True):
                reasons[metric_id] = str(declaration.missing_reason())
        return reasons

    def _compose_info_segments(self) -> list[_InfoSeg]:
        """把"这一帧要画的信息"组合成段列表（数据，不是绘制串）。

        首段恒为主信息段（信息槽内容由 ``info_mode`` 决定），其后按
        ``island_metrics`` 的列表序追加指标段。信息槽整体关掉时返回空列表——
        "没有段"与"首段不是 primary"是两件事。

        **已知但不可用的段（``_unavailable_segments``）不在这里**：它进不了段列表，
        所以胶囊连 ``--`` 占位都不会画（收缩流水线看不到它、+N 也折不到它）。
        这是"不占位"的机器保证——不是靠某个分支不画，而是它根本不在列表里。

        这是**未收缩**的全量列表（宽度白名单与采样口径都以它为准）；实际画出来的
        行由 :meth:`_row_plan` 给出的收缩结果决定（折尾段 → +N chip、省名字）。
        """
        if not self._visible_parts()[2]:
            return []
        unavailable = self._unavailable_segments()
        segments = [self._primary_segment()]
        for metric_id in self._metric_ids():
            if metric_id in unavailable:
                continue
            if metric_id == island_segments.NETWORK:
                segments.append(self._network_segment())
            elif metric_id == island_segments.GPU:
                segments.append(self._gpu_segment())
            elif metric_id == island_segments.FPS:
                segments.append(self._fps_segment())
            elif metric_id in _DEVICE_LABELS:
                segments.append(self._device_segment(metric_id))
            elif self._extension_declaration(metric_id) is not None:
                # 第三方段（声明清单里的组件，或本进程 ``register_segment`` 注册过的段）：
                # 与内置段走同一条段列表/排版/折叠路径——"注册即可上岛"在**配置准入**
                # 之后成立（准入由声明清单决定，见 config._clean_island_metrics）。
                segments.append(self._extension_segment(metric_id))
            else:
                segments.append(self._unknown_segment(metric_id))
        return segments

    def _character_name(self) -> str:
        """岛上显示的角色名（用户别名优先，回退角色 id）。

        取源是 ``set_identity_provider`` 注入的**当前身份视图**——``character`` 是
        每宠键，本窗口的 ``config`` 只有出厂默认的残留，读它会让换完角色的岛名
        停在默认那只。未注入时退回 ``self.config``（直接构造岛的场合）。
        """
        source = self.config if self._identity_provider is None else self._identity_provider()
        character_id = str(source.get("character", catalog.DEFAULT_CHARACTER))
        return source.character_alias(character_id) or character_id

    def _icon_spec(self) -> str:
        """图标配置原值（auto / img:<路径> / 文字或 emoji）。"""
        return str(self._cfg.get("icon") or _ICON_AUTO).strip()

    def _icon_image_mode(self) -> bool:
        """当前图标是否走图片路径（auto / img:）——只有文字模式才允许画字符。"""
        spec = self._icon_spec()
        return spec == _ICON_AUTO or spec.startswith(_ICON_IMG_PREFIX)

    def _clear_icon_cache(self) -> None:
        """丢弃头像位图与内容层位图；换代计数自增让缓存 key 必然失配。"""
        self._icon_pixmap_cache = None
        self._icon_img_failed = False
        self._icon_pixmap_token += 1
        self._content_pixmap = None
        self._content_cache_key = None

    def _icon_text(self) -> str:
        """文字/emoji 模式画在底圈里的字符（用户主动选择才走这条路）。

        默认 icon="auto" 不经过这里：进程内第一次用 QPainter/QLabel 画 emoji
        字符会触发 DirectWrite 彩色 emoji 字体栈加载，实测定案一次性 +33.6MB
        私有内存（纯文字窗口 61.5MB → 画一个 🐳 后 95.1MB，且与 emoji 个数
        无关）。空值兜底也不再回 "🐳"，避免隐形地重新付这笔税。
        """
        return str(self._cfg.get("icon") or "").strip()[:8]

    def _icon_pixmap(self) -> QPixmap | None:
        """图片模式的图标位图；文字/emoji 模式返回 None。

        auto：provider 命中缓存直接返回；未命中才回调取图，成功即缓存，失败
        （帧未就绪）不缓存——下次重建内容层时重试。
        img:：读文件并缓存，**失败结果也缓存**（否则每帧都去碰磁盘）。
        """
        spec = self._icon_spec()
        if spec.startswith(_ICON_IMG_PREFIX):
            if self._icon_pixmap_cache is not None:
                return self._icon_pixmap_cache
            if self._icon_img_failed:
                return None
            path = spec[len(_ICON_IMG_PREFIX):].strip()
            pm = QPixmap(path) if path else QPixmap()
            if pm.isNull():
                self._icon_img_failed = True
                logger.warning("灵动岛自定义图标加载失败：%s", path)
                return None
            self._icon_pixmap_cache = pm
            self._icon_pixmap_token += 1
            return pm
        if spec != _ICON_AUTO or not callable(self._icon_provider):
            return None
        if self._icon_pixmap_cache is not None:
            return self._icon_pixmap_cache
        try:
            pm = self._icon_provider()
        except Exception:
            logger.exception("灵动岛头像回调失败")
            return None
        if not isinstance(pm, QPixmap) or pm.isNull():
            return None
        self._icon_pixmap_cache = pm
        self._icon_pixmap_token += 1
        return pm

    def _icon_pixmap_scaled(self, logical_px: float) -> QPixmap | None:
        """头像按逻辑尺寸出图（乘 DPR + SmoothTransformation，防 hi-dpi 糊）。"""
        pm = self._icon_pixmap()
        if pm is None:
            return None
        dpr = self.devicePixelRatioF()
        edge = max(1, round(logical_px * dpr))
        scaled = pm.scaled(edge, edge, Qt.AspectRatioMode.KeepAspectRatio,
                           Qt.TransformationMode.SmoothTransformation)
        scaled.setDevicePixelRatio(dpr)
        return scaled

    def _shrink_budget(self) -> int:
        """收缩触发线：岛所在屏的可用宽 − 32px（两侧各留一个 ``_EDGE_MARGIN``）。

        无 640 硬帽（产品决策：爱用多少用多少，小屏由收缩流水线兜底）。多屏
        按**岛当前所在屏**取——跨屏迁移后由 :meth:`_update_size` 重算（拖拽过程中
        宽度冻结，不重算）。拿不到屏信息时返回 0 = 不收缩，交给 clamp 兜底。
        """
        screen = self._current_screen()
        if screen is None:
            return 0
        return int(screen.availableGeometry().width()) - _EDGE_MARGIN * 2

    def _plan_inputs(self) -> tuple:
        """行计划的**输入白名单**（也是它的缓存键）：只有这些变化才允许重算。

        - 可见项 / 角色名（含静止态省略后的文本与占位宽）/ 各段的
          ``(id, label, box_text, 单位, 主信息 text)`` —— 段增删与段模板变化都在
          这一项里（单位是模板面：它进的是盒宽样本，不是读数）；
        - 字号各层的度量指纹 + DPR：系统字体/DPI 一变必须重测（字变了盒子也要变）；
        - 收缩预算（屏可用宽 − 32px）：换屏 / 跨屏迁移后要重算收缩级。

        **采样值不在键里**（指标段的 ``text`` 被刻意排除）：它是内容层的事——
        指标段盒宽由声明样本定死，读数在盒内右对齐伸缩。收缩级是这些输入的
        纯函数（见 ``island_layout.plan_info_row``），所以它也不必单独进键。

        段元组的**末位**只有主信息段非空（它是"值"：时间/余额在跳，进键是因为
        现文案超样本时会加宽）。结构签名（:meth:`_structure_key`）要剔除的正是
        这一位，改元组形状时两处必须一起改。
        """
        segments = self._compose_info_segments()
        return (
            self._visible_parts(),
            self._name_layout(),
            tuple(
                (seg.seg_id, seg.label, seg.box_text, seg.unit, seg.elide_px,
                 seg.text if seg.kind == island_layout.KIND_PRIMARY else "")
                for seg in segments
            ),
            self._fonts_key(),
            round(self.devicePixelRatioF(), 3),
            self._shrink_budget(),
        )

    def _row_plan(self):
        """内容行的行计划（含收缩级）——**自然宽与绘制的唯一来源**。

        按 :meth:`_plan_inputs` 缓存：采样值变化命中同一个键，一次都不重算几何。
        拖拽中冻结（拖曳期间不重算收缩级：内容先跳而窗口没变宽，比"松手才换档"
        更容易被看成卡顿）；计划一变就同步折叠态对应的采样订阅。
        """
        key = self._plan_inputs()
        self._plan_inputs_seen = key
        if self._plan_inputs_key == key:
            return self._plan
        if self._dragging and self._plan is not None:
            return self._plan
        plan = self._compute_row_plan()
        self._plan_inputs_key = key
        self._plan = plan
        self._sync_fold_sampling(plan)
        return plan

    def _oversize_extension_ids(self, segments) -> tuple[str, ...]:
        """第三方段里**声明盒宽 >120px** 的那些（注册序；首批折叠候选）。

        判决与告警都走 ``island_segments.judge_oversize``（注册面那本 ``_SEGMENTS``
        与清单声明共用同一份实现）：清单组件从没被 ``register_segment`` 注册过，
        只查注册表会漏掉它们——那正是"超宽先折"在清单路径上失效的原因。
        度量用 13px 那一层（数值盒的字号，段盒的主体），与 ``SegmentDeclaration.width_px``
        的公式一致（label + 4 + max_text + 1 + unit）。
        """
        extension_ids = [seg.seg_id for seg in tuple(segments)[1:]
                         if seg.seg_id not in island_segments.BUILTIN_SEGMENT_IDS]
        if not extension_ids:
            return ()
        value_fm = self._metrics_for("value")
        oversize: list[str] = []
        for segment_id in extension_ids:
            declaration = self._extension_declaration(segment_id)
            if declaration is None:
                continue
            probe = (declaration if isinstance(declaration, island_segments.SegmentDeclaration)
                     else declaration.probe_declaration())
            try:
                if island_segments.judge_oversize(probe, value_fm):
                    oversize.append(segment_id)
            except Exception:  # pragma: no cover - 度量对象异常：那一段不参与首批候选
                logger.warning("超宽段判决失败：%s（退化为尾部优先）", segment_id,
                               exc_info=True)
        return tuple(oversize)

    def _compute_row_plan(self):
        """按可见项与预算算一次行计划（纯函数调用，除缓存外不改状态）。

        右侧账目（E7 修正）：``trail`` 无条件含与左侧对称的 19px 内容边距——
        修复前状态点右缘 = 窗口宽（右半 6px 画出胶囊轮廓）、``show_status``
        关时末段直接顶窗口边；补 19 后右缘与左侧同为「6 内缩 + 13 边距」。
        """
        icon, name, _info, status = self._visible_parts()
        segments = self._compose_info_segments()
        lead = _CONTENT_PAD_PX + ((_ICON_PX + _ICON_GAP_PX) if icon else 0.0)
        trail = _CONTENT_PAD_PX + ((_STATUS_GAP_PX + _STATUS_DOT_PX) if status else 0.0)
        # 首批折叠候选 = 声明盒宽 >120px 的**第三方**段（登记在案的那几个）：
        # 判决走 ``island_segments.judge_oversize``（与 ``oversize_segment_ids``
        # 同一份实现 + 同一份"按字体只告警一次"的去重），所以超宽段先被折，哪怕它
        # 不在列表尾部。内置段不在候选里——它们的盒宽是产品自己的模板，不受第三方
        # 120px 规则约束（改了会连带改动内置段的既有折叠次序）。
        fold_first = self._oversize_extension_ids(segments)
        return island_layout.plan_info_row(
            segments,
            lead_w=lead,
            trail_w=trail,
            name_text=self._character_name() if name else "",
            available_w=self._shrink_budget(),
            primary_fm=self._metrics_for("primary"),
            value_fm=self._metrics_for("value"),
            label_fm=self._metrics_for("label"),
            name_fm=self._metrics_for("name"),
            fold_first_ids=fold_first,
        )

    def _structure_key(self, plan) -> tuple:
        """结构签名（淡化的唯一判据）：宽度白名单键去掉主信息**现文案**。

        为什么剔除现文案：时间/余额是"值"，每秒/每 30s 在跳；照字面拿整个
        :meth:`_plan_inputs` 当判据，时间跳分与余额刷新都会各播一次淡化，违反
        "采样值换字零动画"的硬规则。剔除之后剩下的分量正好是施工单列的四类
        结构变化——段增删（段元组长度与 id 序）、折叠展开（level / folded / name）、
        主信息模式切换（盒样本与省略宽是模板面）、收缩级变化（level 与预算）。

        复用 ``_plan_inputs_seen``（:meth:`_row_plan` 本帧刚算过的那份）而不是重算：
        重算一次实测 ~45µs，而绘制每帧都过这里（拖拽期 60fps）。后缀
        level/name/folded 与 :meth:`_width_key` 同源（是输入的纯函数，加进来让
        "折叠↔展开"这类结构变化在签名上肉眼可见）；拖拽中 :meth:`_row_plan` 返回
        冻结计划，签名随之冻结——拖拽期间不播正靠这一点。
        """
        visible, name_layout, segments, fonts, dpr, budget = self._plan_inputs_seen
        # 段元组末位 = 主信息现文案（见 _plan_inputs 的"值"分量说明）
        return (visible, name_layout, tuple(item[:5] for item in segments),
                fonts, dpr, budget, plan.level, plan.name, plan.folded)

    def _capsule_width(self) -> int:
        """自然宽 = 行计划给出的宽度（左右各 19px 内边距，见设计稿 §1.1）。

        宽度白名单的落地：**只在 :meth:`_plan_inputs` 变化时重算一次**（缓存），
        而采样值进不了那个键——所以"数字跳动不推邻居"是结构保证。指标段盒宽 =
        声明样本的实测盒宽，读数在盒内右对齐伸缩；主信息段盒宽 = 样本与现文案的
        较宽者（长余额串按现文案加宽，不静默吃字）。收缩（省名字 / 折 +N / 省
        custom 主信息）只改段列表与名字态，同样从这条路径出去。
        """
        return self._row_plan().width

    def _name_layout(self) -> tuple[str, int]:
        """角色名的**静止态**显示文本与占位宽（≤96px 右省略）。

        这是宽度白名单里的"名字分量"；S1（收到 40px）与 S2（整段隐藏）在
        ``island_layout.plan_info_row`` 里另算——三处省略共用
        :func:`island_layout.elide_to_width` 同一份实现，串不会各自漂移。
        """
        metrics = self._metrics_for("name")
        display = island_layout.elide_to_width(
            self._character_name(), _NAME_MAX_PX, metrics)
        return display, int(metrics.horizontalAdvance(display))

    def _width_key(self) -> tuple:
        """自然宽白名单键：**只有这些输入变化**才允许重算自然宽。

        内容 = 输入白名单（见 :meth:`_plan_inputs`）+ 本次收缩的**结构签名**
        （级 / 名字实际显示态 / 折叠段）。签名是输入的纯函数，加进来是为了让
        "折叠↔展开"这类结构变化在白名单上肉眼可见地可验（它同时也是
        :meth:`_row_plan` 的缓存键的一部分，两者不可能不一致）。
        """
        plan = self._row_plan()
        return self._plan_inputs() + (plan.level, plan.name, plan.folded)

    def _update_size(self) -> None:
        """重算窗口几何（宽度白名单的落点；采样值永不走到这里）。

        两条纪律：

        - **拖拽中短路**：拖曳期间宽度冻结（松手落定后由 mouseReleaseEvent 重算），
          否则 island 会在光标底下随内容/换屏抽动；
        - **伸长按三分区锚点**：自由胶囊变宽时固定"贴屏那一侧的边"（左 1/3 固定
          左缘 / 右 1/3 固定右缘 / 中间固定中心，见 :func:`island_layout.anchored_x`）
          ——贴边的岛直接向右长会先长出屏再被 clamp 顶回来。停靠细条与展开卡片
          不走锚点：它们的几何由 ``_target_rect`` 贴边决定。
        """
        if self._dragging:
            return
        size = self._rest_size()
        x = self.x()
        if self._mode == "normal":
            screen = self._current_screen()
            if screen is not None:
                available = screen.availableGeometry()
                x = island_layout.anchored_x(
                    x=x, width=self.width(), new_width=size.width(),
                    screen_left=available.left(), screen_width=available.width())
        self._set_free_geometry(QRect(QPoint(x, self.y()), size))
        self._apply_fixed_size()

    def _sync_fold_sampling(self, plan) -> None:
        """折叠态变化 → 采样订阅跟着走（被折进 +N 的段撤销订阅）。

        性能契约而不是视觉细节：看不见的段不该继续烧 psutil/探测包（见设计稿
        §3.2 的"采样联动"）。展开时按既有 ``_sync_*_slot`` 路径重新订阅——
        网络段是整条采样器重建、设备槽按 id 重新订阅且首轮报 sampling（不拿折叠
        前的读数假装"它一直在采"），两处都在 ``_sync_*_slot`` 里收口。
        """
        folded = frozenset(plan.folded)
        if folded == self._folded_metrics:
            return
        self._folded_metrics = folded
        self._sync_network_slot()
        self._sync_sys_slot()
        self._sync_extension_slot()
        self._sync_fps_slot()

    def _sync_content_sampling(self) -> None:
        """停靠细条态变化 → 采样订阅跟着走（细条态内容全不画 = 全停采样）。

        P2 Slice E6（P1 D2 的越界发现转正）。退订口径与折叠态**完全一致**
        （``_sync_*_slot`` 的收口分支：网络段整条 close、设备槽按 id unsubscribe，
        0 订阅零 syscall）；出细条按同一条路径恢复订阅，首轮按四态报 ``--``——
        细条期间的陈旧读数不许被端出来假装"它一直在采"。

        调用点 = **所有会翻 ``_mode`` / ``_hover_peek`` 的地方**（两处标志一变，
        细条态就可能翻转）：``_apply_position``（构造与设置保存到停靠边）、
        ``expand_card`` / ``collapse_card``、``refresh_from_config``（关掉
        「靠边半隐藏」的复位）、``mouseMoveEvent``（拖出停靠）、``mouseReleaseEvent``
        （细条单击滑出 / 拖拽落定停靠）、``enterEvent`` / ``_dock_back``（hover
        滑出与收回）。状态记忆在 ``_strip_sampling_off``，与折叠态同一套
        "变了才同步"的口径——重复调用是幂等的。
        """
        hidden = self._strip_hidden()
        if hidden == self._strip_sampling_off:
            return
        self._strip_sampling_off = hidden
        self._sync_network_slot()
        self._sync_sys_slot()
        self._sync_extension_slot()
        self._sync_fps_slot()

    def _rest_size(self) -> QSize:
        """静止尺寸：收起 = 胶囊自然宽；展开 = 卡片宽 × (胶囊高 + 卡片高)。

        卡片态两轴都是**实测**的：宽 = max(胶囊自然宽, 最宽指标行 + 32)（钳在
        340..420，见 :meth:`_card_width`），高 = ``_CAPSULE_HEIGHT`` + 四分区账目
        的和（见 :meth:`_card_height`）——不再有固定的 148。
        """
        width = self._capsule_width()
        if self._mode == "expanded":
            return QSize(self._card_width(), _CAPSULE_HEIGHT + self._card_height())
        return QSize(width, _CAPSULE_HEIGHT)

    def _apply_fixed_size(self) -> None:
        size = self._rest_size()
        if self._mode == "docked" and not self._hover_peek:
            edge = self.dock_edge
            if edge in ("top", "bottom"):
                size = QSize(size.width(), _STRIP_THICKNESS)
            elif edge in ("left", "right"):
                size = QSize(_STRIP_THICKNESS, _STRIP_SIDE)
        self.setFixedSize(size)

    def _clamp_rect(self, rect: QRect) -> QRect:
        screen = self._current_screen()
        if screen is None:
            return rect
        available = screen.availableGeometry()
        x = max(available.left(), min(rect.x(), available.right() - rect.width() + 1))
        y = max(available.top(), min(rect.y(), available.bottom() - rect.height() + 1))
        return QRect(QPoint(x, y), rect.size())

    def _target_rect(self) -> QRect:
        """当前模式下的目标矩形（停靠细条 / 滑出预览 / 正常胶囊 / 展开卡片）。"""
        size = self._rest_size()
        edge = self.dock_edge
        screen = self._current_screen()
        if self._mode == "docked" and not self._hover_peek and screen is not None:
            # 参考矩形锚定当前窗口的真实几何中心，而不是"细条左上角 +
            # 胶囊尺寸"：动画收尾（_on_anim_tick 到位后的重算、
            # _finish_animations、showEvent）调用本函数时窗口已是 64 高
            # 细条，旧拼法的中心凭空高出 (64-44)/2 = 10px，每次收回/停靠
            # 动画结束细条就上跳一截，反复悬停累计上窜
            rect = QRect(self.pos(), size)
            rect.moveTop(self.y() + self.height() // 2 - size.height() // 2)
            return strip_rect_for(rect, edge, screen.availableGeometry())
        if edge != "none" and self._mode in ("docked", "expanded") \
                and screen is not None:
            # 从停靠边向内展开：顶/底锚定该边，左右以细条纵向中心展开
            available = screen.availableGeometry()
            rect = QRect(self.pos(), size)
            if edge == "top":
                rect.moveTop(available.top())
            elif edge == "bottom":
                rect.moveBottom(available.bottom())
            elif edge == "left":
                rect.moveLeft(available.left())
                rect.moveTop(self.y() + self.height() // 2 - size.height() // 2)
            elif edge == "right":
                rect.moveRight(available.right())
                rect.moveTop(self.y() + self.height() // 2 - size.height() // 2)
            return self._clamp_rect(rect)
        return self._clamp_rect(QRect(self.pos(), size))

    def _screen_at(self, point: QPoint):
        """按给定全局坐标选屏；落在多屏空洞（错位拼接）时回退主屏。"""
        return QGuiApplication.screenAt(point) or QGuiApplication.primaryScreen()

    def _current_screen(self):
        screen = self._screen_at(self.pos())
        return screen

    def _apply_position(self) -> None:
        self._update_size()
        screen = self._current_screen()
        available = screen.availableGeometry() if screen is not None else None
        # 坐标的合法性判据与 config 的清洗层共用一处（`config._clean_island_coordinate`：
        # 有限 + ±100000 内）。这里不是多余的第二遍清洗——配置对象可能**没走清洗**
        # （手改配置、跨版本回滚、外部工具写盘），而 ``QPoint(int(x), int(y))`` 对
        # 10**100 抛 OverflowError、对 nan/inf 抛 ValueError/OverflowError，会在
        # 应用创建路径上直接崩掉。非法值走默认定位分支，与字符串/列表坐标的既有
        # 回退同一条路（注意：只查 ``math.isfinite`` 挡不住 10**100）。
        x = _clean_island_coordinate(self._cfg.get("x"))
        y = _clean_island_coordinate(self._cfg.get("y"))
        if x is not None and y is not None:
            pos = QPoint(int(x), int(y))
        else:
            pos = (
                QPoint(available.right() - self.width() - _EDGE_MARGIN,
                       available.top() + _EDGE_MARGIN)
                if available else QPoint(100, 100)
            )
        self.move(pos)
        self._clamp_to_screen()
        # 展开卡片态不被停靠劫持：卡片是用户当前的交互焦点，设置保存触发的
        # 位置应用不能把 _mode 改成 docked（卡片会留在 16px 细条上收不起来）；
        # 收起时 collapse_card 会按 dock_edge 自行决定是否停靠
        if self.dock_edge != "none" and self._mode != "expanded":
            self._mode = "docked"
            self._hover_peek = False
            target = self._target_rect()
            self._set_free_geometry(target)
            self._apply_fixed_size()
        self._sync_content_sampling()

    def _clamp_to_screen(self) -> None:
        if self._mode == "docked" and not self._hover_peek:
            return
        screen = self._current_screen()
        if screen is None:
            return
        available = screen.availableGeometry()
        x = max(available.left(), min(self.x(), available.right() - self.width() + 1))
        y = max(available.top(), min(self.y(), available.bottom() - self.height() + 1))
        if (x, y) != (self.x(), self.y()):
            self.move(x, y)
            # 位置校正也要通知碰撞体重提交，否则碰撞体留在旧位置
            self._emit_geometry_changed()

    def _save_position(self) -> None:
        island = dict(self._cfg)
        island["x"] = self.x()
        island["y"] = self.y()
        self._cfg = island
        self.config.set("dynamic_island", island)
        self.config.save()

    def _save_dock_edge(self, edge: str) -> None:
        island = dict(self._cfg)
        island["dock_edge"] = edge if edge in _DOCK_EDGES else "none"
        self._cfg = island
        self.config.set("dynamic_island", island)
        self.config.save()

    # ------------------------------------------------------------ 绘制
    def _style_palette(self):
        """返回 (背景, 主文字色, 次文字色)。背景可为 QColor 或 QLinearGradient。

        所有风格的 alpha 都乘上不透明度设置。
        """
        opacity = self._opacity()
        style = str(self._cfg.get("style") or "dark")
        if style == "light":
            return (QColor(255, 255, 255, round(242 * opacity)),
                    QColor(31, 35, 40), QColor(107, 114, 128))
        if style == "glass":
            # 四停点：0/1 是玻璃的上下表面，0.48/0.52 的折线是"玻璃折射线"
            # ——中间那道亮暗交替让渐变压出一道折射边，三段插值各管一段。
            gradient = QLinearGradient(0, 0, 0, self.height())
            gradient.setColorAt(0.00, QColor(255, 255, 255, round(214 * opacity)))
            gradient.setColorAt(0.48, QColor(234, 244, 255, round(168 * opacity)))
            gradient.setColorAt(0.52, QColor(220, 235, 255, round(146 * opacity)))
            gradient.setColorAt(1.00, QColor(244, 249, 255, round(196 * opacity)))
            return gradient, QColor(35, 45, 60), QColor(90, 105, 125)
        return (QColor(28, 30, 38, round(235 * opacity)),
                QColor(235, 238, 245), QColor(160, 170, 190))

    def _keyline_color(self) -> QColor:
        """深色底上的 1px 分界描边（暗壁纸下把胶囊轮廓勾出来）。"""
        style = str(self._cfg.get("style") or "dark")
        if style in ("light", "glass"):
            return QColor(0, 0, 0, 28)
        return QColor(255, 255, 255, 52)

    def _divider_color(self) -> QColor:
        """段间分隔竖线的颜色：比 keyline 再弱一档。

        轮廓线管"窗在哪"，分隔线只管"段在哪"——两者同色会让胶囊边缘与段边界
        抢注意力（暗 `#FFF`α36 / 亮 `#1F2328`α31 / glass `#232D3C`α36）。
        """
        style = str(self._cfg.get("style") or "dark")
        if style == "light":
            return QColor(31, 35, 40, 31)
        if style == "glass":
            return QColor(35, 45, 60, 36)
        return QColor(255, 255, 255, 36)

    def _icon_ring_color(self) -> QColor:
        """头像底圈的内侧细光圈色：暗/玻璃底白 α46、亮底黑 α20。

        亮底给白圈等于没画（白圈落在亮色底圈上分不出来），所以按 style 分支给
        常量——与 ``_keyline_color``/``_bevel_colors`` 同款口径。它随 style 变化，
        所以 ``style`` 必须进内容缓存键（见 :meth:`_content_cache`）。
        """
        style = str(self._cfg.get("style") or "dark")
        return QColor(*(_ICON_RING_LIGHT if style == "light" else _ICON_RING_DARK))

    def _alert_color(self, severity: str) -> QColor | None:
        """告警级的**数值**色（``""``/未识别 → None，表示不染色）。

        暗底直接用规格值（``#F0B44C`` / ``#F2777A``）；亮底与玻璃底按**同一色相
        加深档**——那两个底色接近白，规格色在白底上对比度只有 1.85:1 / 2.73:1，
        13px 数字读不出来。加深档取"色相不变、亮度下降"（38°/358° 不变，
        158→98 / 181→112），白底对比度 4.53:1 / 6.21:1。口径跟着既有主题函数
        （``_divider_color`` / ``_keyline_color`` 同款 style 分支），不新增主题键。

        只返回颜色，不改任何传入对象——调用方复用的主文字色不该被就地改坏
        （与 :meth:`_unit_color` 同一纪律）。
        """
        if severity == island_segments.ALERT_CRIT:
            dark, light = _ALERT_CRIT_DARK, _ALERT_CRIT_LIGHT
        elif severity == island_segments.ALERT_WARN:
            dark, light = _ALERT_WARN_DARK, _ALERT_WARN_LIGHT
        else:
            return None
        style = str(self._cfg.get("style") or "dark")
        return QColor(light if style in ("light", "glass") else dark)

    @staticmethod
    def _unit_color(base: QColor) -> QColor:
        """单位盒的颜色：与数值同色、alpha ×70%（裁决稿 §二-5 的"单位弱化"）。

        弱化靠双管——字号降到 10px 层（L4）+ 这里按比例降色；缺了降色，13px 与
        10px 之间只差 1px，"弱化不足"。

        用 QColor 的 alpha 而不是 ``painter.setOpacity``：内容层是缓存位图，绘制
        中途的 opacity 一旦忘记还原就会污染后面的图元（状态点/竖线），而 alpha
        只作用在这几个字形上，且能被测试直接断言。**不改传进来的颜色对象**——
        调用方复用的主文字色不该被这里就地改坏。
        """
        color = QColor(base)
        color.setAlpha(round(color.alpha() * _UNIT_ALPHA))
        return color

    @staticmethod
    def _readable_card_background(background):
        """展开卡片底板：alpha 钳到下限 215。

        胶囊透明度设置只该影响胶囊本体（不挡视线）；卡片是阅读界面，
        跟着透明化会让余额/消息直接糊在壁纸上（实机反馈）。QColor 与
        glass 的 QLinearGradient 两种背景分别钳制。
        """
        if isinstance(background, QLinearGradient):
            clamped = QLinearGradient(background)
            for pos, color in background.stops():
                c = QColor(color)
                c.setAlpha(max(c.alpha(), 215))
                clamped.setColorAt(pos, c)
            return clamped
        c = QColor(background)
        c.setAlpha(max(c.alpha(), 215))
        return c

    def _status_dot_state(self) -> str:
        """状态点五态解析（优先级 错误 > 待确认 > 工作 > 空闲/隐藏）。

        输入是两个通道：``_dsh_state``（八态收敛，权威源，见 :meth:`set_dsh_state`）
        与 ``_agent_active``（旧粗粒度活跃位，只在收敛态缺席时补位）。完成是**瞬态**：
        它是空闲点位的 4s 替身（盖过空闲，但盖不过工作/待确认/错误/隐藏——那些一出现
        就把它顶掉，见 :meth:`set_dsh_state` 的同源收口）。
        """
        state = self._dsh_state
        if state == DshState.ERROR.value:
            # 错误不能自动恢复：同刻只画一个图元时它必须最高优先
            return _DOT_ERROR
        if state in (DshState.WAITING_APPROVAL.value,
                     DshState.WAITING_QUESTION.value):
            # 待确认/待回答：读收敛器的锁存位（gated 口径在 dsh_state 里）
            return _DOT_WAITING
        if state in (DshState.WORKING.value,
                     DshState.THINKING.value) or self._agent_active:
            return _DOT_WORKING
        if not self._pet_visible or state == DshState.OFFLINE.value:
            # 宠物隐藏 / DSH 离线：没有可看的东西，空心环说实话。它盖过完成瞬态
            # ——空心环是唯一能说明"宠物不在/DSH 没在跑"的信号，不让 4s 绿点把它
            # 遮掉（完成只是空闲点位的临时替身，见下）
            return _DOT_HIDDEN
        if self._dot_done:
            # 完成 = 空闲点位的 4s 替身（success 进入时亮，到点回落空闲）
            return _DOT_DONE
        return _DOT_IDLE

    def _status_dot_style(self) -> tuple[str, QColor]:
        """状态点的 (形状, 颜色)——绘制与内容层缓存键的唯一来源。"""
        state = self._status_dot_state()
        if state == _DOT_ERROR:
            return _DOT_GLYPH, QColor(_DOT_ERROR_COLOR)
        if state == _DOT_WAITING:
            return _DOT_HOLLOW, self._accent_color()
        if state == _DOT_WORKING:
            return _DOT_SOLID, self._accent_color()
        if state == _DOT_DONE:
            return _DOT_SOLID, QColor(_DOT_DONE_COLOR)
        if state == _DOT_HIDDEN:
            return _DOT_HOLLOW, self._status_dot_neutral()
        return _DOT_SOLID, self._status_dot_neutral()

    def _status_dot_neutral(self) -> QColor:
        """中性色 = 主题次色（空闲实心点与隐藏/离线空心环共用）。"""
        _background, _primary, secondary = self._style_palette()
        return QColor(secondary)

    def _status_dot_color(self) -> QColor:
        """状态点颜色（形状与优先级见 :meth:`_status_dot_style`）。"""
        return self._status_dot_style()[1]

    def _paint_status_dot(self, painter: QPainter, rect: QRectF) -> None:
        """画状态点（8px 尺寸不变）：形状+颜色双通道，同刻只画一个图元。

        空心环的描边内缩半个线宽，外缘仍占满整个 8px 足迹；感叹是矢量路径两笔
        （见 :func:`_status_error_glyph_path`）。胶囊与停靠细条共用本方法——同一
        图元语言，两处不可能画出两套样子。
        """
        shape, color = self._status_dot_style()
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(Qt.PenStyle.NoPen)
        if shape == _DOT_HOLLOW:
            pen = QPen(color, _DOT_RING_PX)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            inset = _DOT_RING_PX / 2.0
            painter.drawEllipse(rect.adjusted(inset, inset, -inset, -inset))
        else:
            painter.setBrush(color)
            painter.drawEllipse(rect)
        if shape == _DOT_GLYPH:
            ink = QColor(_DOT_GLYPH_COLOR)
            ink.setAlpha(235)
            pen = QPen(ink, rect.height() * _DOT_GLYPH_W_RATIO)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(pen)
            painter.drawPath(_status_error_glyph_path(rect))
        if shape != _DOT_HOLLOW:
            # 珠感高光：实心/感叹点画完后在**上半约 1/3** 叠一道半椭圆白高光，
            # 点从"一块纯色圆盘"变成"一颗珠子"。空心环不画——环内本来是空的，
            # 高光会把环画回实心（形状通道就废了）。尺寸仍是 8px：高光全在点内。
            painter.setBrush(QColor(255, 255, 255, _DOT_HIGHLIGHT_ALPHA))
            painter.drawEllipse(QRectF(
                rect.x() + _DOT_HIGHLIGHT_INSET_X, rect.y() + _DOT_HIGHLIGHT_TOP,
                rect.width() - 2 * _DOT_HIGHLIGHT_INSET_X,
                rect.height() * _DOT_HIGHLIGHT_H_RATIO))
        # 复位画笔/画刷：调用点后面还可能画别的东西
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(Qt.BrushStyle.NoBrush)

    def _status_dot_key(self) -> tuple[str, str]:
        """内容层缓存键的「状态点」分量：形状+颜色。

        形状必须进键：空心环与实心点可能同色（中性环 vs 中性实心点），
        但像素不同，同色也必须各自成键。它**不进**宽度白名单键。
        """
        shape, color = self._status_dot_style()
        return shape, color.name()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # 展开卡片时跳过一切整窗变换（kick 平移/呼吸/倾斜/缩放）：卡片底板
        # 贴窗口边缘绘制，任何位移或 >1 缩放都会把它顶出窗口被硬切（截边
        # 实机反馈；kick 来自展开后的余额刷新弹跳，是必然路径）
        if self._mode == "expanded":
            tilt, scale = 0.0, 1.0
        else:
            # 动效变换：整数位移（文字锐利）+ 呼吸微浮动
            kick_x = round(self._kick_x)
            kick_y = round(self._kick_y)
            if time.monotonic() < self._breathe_until:
                kick_y += round(1.5 * math.sin(time.monotonic() * 2.6))
            if kick_x or kick_y:
                painter.translate(kick_x, kick_y)
            tilt = max(-_TILT_MAX_DEG, min(_TILT_MAX_DEG, self._tilt))
            scale = max(_SCALE_MIN, min(_SCALE_MAX, self._scale))
        if tilt or scale != 1.0:
            cx = self.width() / 2.0
            cy = min(self.height(), _CAPSULE_HEIGHT) / 2.0
            painter.translate(cx, cy)
            painter.rotate(tilt)
            painter.scale(scale, scale)
            painter.translate(-cx, -cy)

        if self._mode == "docked" and not self._hover_peek:
            self._paint_strip(painter)
        else:
            self._paint_capsule(painter)
        painter.end()

    def _squished_capsule_rect(self) -> tuple[QRectF, float]:
        """果冻形变后的胶囊绘制矩形（内缩 _CAPSULE_INSET 留形变余量）。

        只返回外形的几何——文字/图标不在这里面变换，保持锐利。
        squish<1 时竖向压扁、横向鼓出（面积大致守恒），绕胶囊中心形变。
        """
        base = QRectF(_CAPSULE_INSET, _CAPSULE_INSET,
                      self.width() - _CAPSULE_INSET * 2,
                      _CAPSULE_HEIGHT - _CAPSULE_INSET * 2)
        # 展开卡片态冻结形变（与整窗变换禁用同口径）：squish 鼓出会把
        # 贴边底板两侧的胶囊圆角削掉（实测 342.8px > 340px 窗口宽）
        squish = 1.0 if self._mode == "expanded" else \
            max(_SQUISH_MIN, min(_SQUISH_MAX, self._squish))
        if abs(squish - 1.0) < 0.002:
            return base, base.height() / 2.0
        sy = squish
        # 竖压横鼓（权重打折，鼓出不超出内缩余量）；横向**只鼓不缩**（R1 评审修复：
        # sy>1 拉伸期 sx<1 会让外壳比静止态窄，宽岛的头像/状态点画到外壳外——
        # 内容不随形变，外壳横向必须始终 ≥ 静止宽）。
        sx = 1.0 + max(0.0, 1.0 - sy) * 0.3
        w, h = base.width() * sx, base.height() * sy
        cx, cy = base.center().x(), base.center().y()
        return QRectF(cx - w / 2.0, cy - h / 2.0, w, h), h / 2.0

    def _paint_strip(self, painter: QPainter) -> None:
        """停靠细条：圆角条 + 靠屏侧主题色亮线 + 状态点（竖条加图标）。"""
        rect = QRectF(0, 0, self.width(), self.height())
        radius = min(rect.width(), rect.height()) / 2.0
        background, primary, _secondary = self._style_palette()
        opacity = self._opacity()
        if isinstance(background, QColor):
            base = QColor(background)
            base.setAlpha(min(255, round(210 * opacity)))
        else:
            base = QColor(255, 255, 255, round(210 * opacity))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(base)
        painter.drawRoundedRect(rect, radius, radius)

        # 靠屏一侧的 2px 亮线（深壁纸上也能一眼找到细条）
        accent = QColor(self._accent_color())
        accent.setAlpha(220)
        painter.setBrush(accent)
        edge = self.dock_edge
        if edge == "top":
            painter.drawRoundedRect(QRectF(6, 0, rect.width() - 12, 2), 1, 1)
        elif edge == "bottom":
            painter.drawRoundedRect(QRectF(6, rect.height() - 2, rect.width() - 12, 2), 1, 1)
        elif edge == "left":
            painter.drawRoundedRect(QRectF(0, 6, 2, rect.height() - 12), 1, 1)
        elif edge == "right":
            painter.drawRoundedRect(QRectF(rect.width() - 2, 6, 2, rect.height() - 12), 1, 1)

        dot_size = _STATUS_DOT_PX
        if edge in ("left", "right"):
            # 竖条：上方角色图标、下方状态点。头像走 drawPixmap（不碰文字栈）；
            # emoji 文字模式维持原小字号（11px）防裁切；auto 模式 provider 没
            # 就绪就只留状态点，绝不回退去画 🐳（那等于白付 33MB 税额）
            icon_pm = self._icon_pixmap_scaled(_ICON_STRIP_PX)
            if icon_pm is not None:
                painter.drawPixmap(
                    QPointF((rect.width() - _ICON_STRIP_PX) / 2.0, 8.0), icon_pm)
            elif not self._icon_image_mode():
                painter.setPen(primary if isinstance(primary, QColor) else QColor(31, 35, 40))
                icon_font = painter.font()
                icon_font.setPixelSize(11)
                painter.setFont(icon_font)
                fm = painter.fontMetrics()
                painter.drawText(
                    QRectF(0, 8, rect.width(), fm.height()),
                    Qt.AlignmentFlag.AlignCenter, self._icon_text())
                painter.setPen(Qt.PenStyle.NoPen)
            self._paint_status_dot(painter, QRectF(
                (rect.width() - dot_size) / 2, rect.height() - dot_size - 8,
                dot_size, dot_size))
        else:
            self._paint_status_dot(painter, QRectF(
                (rect.width() - dot_size) / 2, (rect.height() - dot_size) / 2,
                dot_size, dot_size))

    # ------------------------------------------------------------ 软陶笔/淡化
    def _bevel_colors(self) -> tuple[QColor, QColor]:
        """软陶笔的 (上缘高光色, 下缘暗边色)——按 style 分支取常量（k3 §7.1-§7.3）。

        与 ``_keyline_color`` / ``_divider_color`` 同款口径：按 style 给常量、
        不新增主题键、不乘 ``_opacity``（keyline 也不乘——外壳质感是"表面"，
        不跟内容透明度一起退）。亮底的白高光在纯白上几乎不可见，保留对称结构，
        亮风的厚度由底部黑暗边承托；玻璃已有强外描边，内高光减半防双线感。
        """
        style = str(self._cfg.get("style") or "dark")
        if style == "light":
            return QColor(*_BEVEL_HIGHLIGHT_LIGHT), QColor(*_BEVEL_SHADOW_LIGHT)
        if style == "glass":
            return QColor(*_BEVEL_HIGHLIGHT_GLASS), QColor(*_BEVEL_SHADOW_GLASS)
        return QColor(*_BEVEL_HIGHLIGHT_DARK), QColor(*_BEVEL_SHADOW_DARK)

    def _paint_bevel(self, painter: QPainter, rect: QRectF, radius: float) -> None:
        """软陶笔：胶囊内缘 1px 环，上半周走高光色、下半周走暗边色。

        三条口径：

        - **画在内缘不在外圈**：外圈已被 keyline 占住（它管"窗在哪"），笔在它里侧
          再 1px（``_BEVEL_INSET``），两者互不盖色——内缩取 1.5 而不是 k3 写的 1：
          1px 笔以路径为中线，内缘行号 = round(rect.top())+1，取 1.5 才让笔落在
          keyline 里侧那一**整行**上（1.0 会落在半像素上，两行各分 50% 糊掉）。
        - **上下半周用裁剪切**：圆角处的内缘是弧线，两条横线替代不了；裁剪是廉价且
          锐利的切法（下半周紧接上半周结束处）。
        - **随 squish 形变**：``rect`` 就是 ``_squished_capsule_rect`` 的产物，
          笔跟外壳一起形变（高光是表面受光，不是贴纸）。
        """
        highlight, shadow = self._bevel_colors()
        inner = rect.adjusted(_BEVEL_INSET, _BEVEL_INSET, -_BEVEL_INSET, -_BEVEL_INSET)
        inner_radius = max(0.0, radius - _BEVEL_INSET)
        half = rect.height() / 2.0
        painter.save()
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setClipRect(QRectF(rect.left(), rect.top(), rect.width(), half))
        painter.setPen(QPen(highlight, 1))
        painter.drawRoundedRect(inner, inner_radius, inner_radius)
        painter.setClipRect(QRectF(rect.left(), rect.top() + half, rect.width(), half))
        painter.setPen(QPen(shadow, 1))
        painter.drawRoundedRect(inner, inner_radius, inner_radius)
        painter.restore()

    def _sync_content_fade(self) -> None:
        """比对**上一帧画出去的结构签名**：变了就起一次淡化。

        签名由 :meth:`_content_cache` 在重建/命中时统一记录（见 ``_painted_structure``）
        ——判据是"两帧之间画出去的结构不同"，不是"这一帧内容变了"：采样值/余额换字
        进不了签名，所以"换字零动画"是结构保证而不是纪律。首帧只登记不播（没有旧
        位图可渐出）。

        拖拽中记签名但不播：拖拽期间内容变化频繁（读数/折叠输入都在动）而窗口几何
        是冻结的，跟着播会把拖拽画成闪屏；签名照记，拖拽结束按**当时**的结构与绘制
        结果比对——松手真的重排了收缩级就照常播一次（不是"补播"，是新的边沿）。
        """
        key = self._painted_structure
        if key is None:
            return
        previous, self._fade_struct_key = self._fade_struct_key, key
        if previous is None or previous == key or self._dragging:
            return
        if self._content_drawn is None:
            return
        self._start_content_fade(self._content_drawn)

    def _start_content_fade(self, previous: QPixmap) -> None:
        """起一次淡化：上一帧的位图按 (1-t) 渐出（``_CONTENT_FADE_MS``）。

        新位图不参与系数——它始终直绘就位（见 ``_paint_capsule`` 的单侧口径）。
        连发的结构变化从上一次的中点重开：先按收尾口径停掉旧表再建新表——两条表
        同时推系数会让帧率翻倍、系数互相打架。
        """
        self._stop_content_fade()
        self._content_fade_from = previous
        self._fade_outgoing_masked = None
        self._fade_masked_key = None
        self._content_fade_t = 0.0
        self._content_fade_last = time.monotonic()
        timer = QTimer(self)
        timer.setInterval(_CONTENT_FADE_TICK_MS)
        timer.timeout.connect(self._on_content_fade_tick)
        self._content_fade_timer = timer
        timer.start()

    def _on_content_fade_tick(self) -> None:
        """按真实帧间隔推进混合系数；到点即停表销毁（停表纪律，同完成瞬态）。"""
        now = time.monotonic()
        elapsed = now - self._content_fade_last
        self._content_fade_last = now
        self._content_fade_t = min(
            1.0, self._content_fade_t + elapsed * 1000.0 / _CONTENT_FADE_MS)
        if self._content_fade_t >= 1.0:
            self._stop_content_fade()
        self.update()

    def _stop_content_fade(self) -> None:
        """停表并销毁淡化定时器（幂等），并跳到终点帧（新位图全不透明）。"""
        timer, self._content_fade_timer = self._content_fade_timer, None
        self._content_fade_from = None
        self._fade_outgoing_masked = None
        self._fade_masked_key = None
        self._content_fade_t = 1.0
        if timer is not None:
            timer.stop()
            timer.deleteLater()

    def _content_cache(self) -> QPixmap:
        """内容层（头像/角色名/信息段/chip/状态灯）的缓存位图。

        为什么缓存（拖岛扫鱼实测定案）：paintEvent 均值 6.2ms，大头是 CJK
        文字的 shaping/回退字体解析；而内容只在刷新（时间跳变/余额刷新/
        配置变更/采样值变化）时变化，拖拽/弹簧期间逐帧重画纯属浪费。缓存后每帧
        一次 blit（亚毫秒）。blit 走同一 painter 的整窗变换（kick 为整数平移，
        文字锐利口径不变）；squish 只调制胶囊外形、本来就不碰内容层。

        key 覆盖所有影响像素的输入（可见项/名字态/各段 text+单位+state+severity/
        字号指纹/颜色/DPR/宽度），任一变化自动重建——**收缩结果（折了哪几段、名字
        显不显示）由段列表与名字进 key**，所以折叠↔展开必然换位图（chip 的告警级
        随 chip 段自己的 ``severity`` 一起进 key）。采样值变化只重建这张位图，
        窗口几何走 ``_capsule_width`` 的白名单，两者互不牵连。
        """
        icon, name, info, status = self._visible_parts()
        plan = self._row_plan()
        # 本帧画出去的结构签名（淡化的判据，见 _sync_content_fade）：在这里记而
        # 不在绘制处另算——本函数已经算过输入键与当刻计划，顺手就是免费（复用
        # ``_plan_inputs_seen``，一帧只算一遍 _plan_inputs）
        self._painted_structure = self._structure_key(plan)
        # 计划只给结构，文案取**当刻**的段列表（见 island_layout.apply_shrink）：
        # 读数每秒在变，计划里的那份快照一转眼就过期
        segments = island_layout.apply_shrink(
            plan, self._compose_info_segments(),
            primary_fm=self._metrics_for("primary"))
        _background, primary_color, secondary_color = self._style_palette()
        # 主信息段（L1）：峰谷模式跟随设置的峰谷色，其余用主文字色
        primary_text_color = self._info_color(primary_color)
        name_text = plan.name
        dpr = self.devicePixelRatioF()
        key = (
            icon, name, info, status,
            self._icon_text() if icon else "",
            name_text if name else "",
            tuple((seg.seg_id, seg.label, seg.text, seg.unit, seg.state, seg.severity,
                   seg.box_text, seg.elide_px)
                  for seg in segments),
            self._accent_color().name() if icon else "",
            # 风格显式进键：头像底圈的内侧细光圈按 style 分档（暗/玻璃白、亮底黑），
            # 而"只有图标"时上面那些颜色项全是空串——不显式进键就会跨风格命中旧位图
            str(self._cfg.get("style") or "dark"),
            primary_color.name() if name else "",
            primary_text_color.name() if segments else "",
            secondary_color.name() if segments else "",
            self._divider_color().name() if len(segments) > 1 else "",
            self._status_dot_key() if status else "",
            self._fonts_key(), dpr, self.width(),
        )
        if self._content_pixmap is not None and self._content_cache_key == key:
            return self._content_pixmap
        # 内容层的命中路径不问 provider（否则拖拽期间每帧取图），只有重建时才问。
        # auto 模式 provider 返回 None 时也不写进 key，于是"没头像"的内容层照样
        # 命中缓存，等下次真正重建（时间/余额/配置/采样变化）时再重试取图。
        icon_pm = self._icon_pixmap_scaled(_ICON_PIXMAP_PX) if icon else None
        if icon_pm is not None:
            key = key + (self._icon_pixmap_token,)
            if self._content_pixmap is not None and self._content_cache_key == key:
                return self._content_pixmap
        pm = QPixmap(max(1, round(self.width() * dpr)),
                     max(1, round(_CAPSULE_HEIGHT * dpr)))
        pm.setDevicePixelRatio(dpr)
        pm.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pm)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        x = _CAPSULE_INSET + 13.0
        baseline = _CAPSULE_HEIGHT / 2.0 + _BASELINE_DY
        painter.setPen(Qt.PenStyle.NoPen)
        if icon:
            circle = QRectF(x, (_CAPSULE_HEIGHT - _ICON_PX) / 2, _ICON_PX, _ICON_PX)
            painter.setBrush(self._accent_color())
            painter.drawEllipse(circle)
            # 内侧 1px 细光圈：把底圈从"一块色斑"勾成"一枚圆章"（暗/玻璃白、
            # 亮底黑）。画在头像之下——头像照旧盖住圈心
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(self._icon_ring_color(), 1))
            painter.drawEllipse(circle.adjusted(0.5, 0.5, -0.5, -0.5))
            painter.setPen(Qt.PenStyle.NoPen)
            if icon_pm is not None:
                # 头像居中画进底圈：drawPixmap 不碰文字/emoji 字体栈
                painter.drawPixmap(
                    QPointF(x + (_ICON_PX - _ICON_PIXMAP_PX) / 2.0,
                            (_CAPSULE_HEIGHT - _ICON_PIXMAP_PX) / 2.0),
                    icon_pm)
            elif not self._icon_image_mode():
                # emoji/文字图标沿用窗口默认字体（用户主动选 emoji 才走这条路）
                painter.setFont(self.font())
                painter.setPen(QColor(255, 255, 255))
                fm = self.fontMetrics()
                painter.drawText(
                    QRectF(x, (_CAPSULE_HEIGHT - fm.height()) / 2 - 1, _ICON_PX, fm.height()),
                    Qt.AlignmentFlag.AlignCenter,
                    self._icon_text(),
                )
                painter.setPen(Qt.PenStyle.NoPen)
            # auto 模式 provider 没就绪：只画底圈，绝不回退画 🐳（那是白付
            # 一次 33MB 的 DirectWrite 彩色 emoji 字体栈税）
            x += _ICON_PX + _ICON_GAP_PX
        if name and name_text:
            # 角色名：L3 12px 次色，静止态右省略到 96px、收缩态收到 40px 或整段隐藏
            # （名字是身份不是数据）——文本与占位宽都来自行计划，绘制层不自己省略
            painter.setFont(self._font_for("name"))
            painter.setPen(primary_color)
            painter.drawText(QPointF(x, baseline), name_text)
            x += plan.name_w + _NAME_GAP_PX
        placements = place_info_segments(
            segments, start_x=x,
            primary_fm=self._metrics_for("primary"),
            value_fm=self._metrics_for("value"),
            label_fm=self._metrics_for("label"))
        for placement in placements:
            seg = placement.seg
            if placement.divider_x is not None:
                # 段间竖线：1×14px、槽位正中；首段前、chip 前与状态点前都不画。
                # 竖直渐变：两端 alpha=0、30%/70% 处才是本色——线头收进段间空隙，
                # 竖线不再是一根两端硬切的 1px 柱子（尺寸与落位一个字节没变）
                divider_color = self._divider_color()
                divider_rect = QRectF(
                    placement.divider_x,
                    (_CAPSULE_HEIGHT - _DIVIDER_H_PX) / 2.0,
                    _DIVIDER_W_PX, _DIVIDER_H_PX)
                divider = QLinearGradient(
                    0, divider_rect.top(), 0, divider_rect.bottom())
                divider.setColorAt(0.0, QColor(
                    divider_color.red(), divider_color.green(),
                    divider_color.blue(), 0))
                divider.setColorAt(0.3, divider_color)
                divider.setColorAt(0.7, divider_color)
                divider.setColorAt(1.0, QColor(
                    divider_color.red(), divider_color.green(),
                    divider_color.blue(), 0))
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(divider)
                painter.drawRect(divider_rect)
            if seg.kind == island_layout.KIND_CHIP:
                # +N chip：描边无填充、圆角 9、高 18，文字居中且无竖线（K3 §3.3）
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.setPen(QPen(self._divider_color(), 1))
                chip_top = (_CAPSULE_HEIGHT - _CHIP_H_PX) / 2.0
                painter.drawRoundedRect(
                    QRectF(placement.box_x, chip_top,
                           placement.box_w, _CHIP_H_PX),
                    _CHIP_RADIUS_PX, _CHIP_RADIUS_PX)
                painter.setFont(self._font_for("label"))
                painter.setPen(secondary_color)
                # chip 文字在**盒内光学居中**（E7 修正）：chip 是容器图元、不与行内
                # 文字比邻，统一基线规则不适用盒内——沿用行基线会让文字在 18px 盒
                # 里偏高 2.5px（审计实测墨中心 19.5 vs 盒中心 22）。
                chip_fm = painter.fontMetrics()
                chip_baseline = chip_top + (
                    _CHIP_H_PX + chip_fm.ascent() - chip_fm.descent()) / 2.0
                painter.drawText(QPointF(placement.text_x, chip_baseline), placement.text)
                if placement.alert_dot_x is not None:
                    # 折叠不埋没告警：被折段含 warn/crit 时 chip 左侧点 4px 色点
                    # （取最高级）。点落在盒内已有内边距里，不改盒宽。
                    alert_color = self._alert_color(seg.severity)
                    if alert_color is not None:
                        painter.setPen(Qt.PenStyle.NoPen)
                        painter.setBrush(alert_color)
                        painter.drawEllipse(QRectF(
                            placement.alert_dot_x,
                            (_CAPSULE_HEIGHT - _CHIP_ALERT_DOT_PX) / 2.0,
                            _CHIP_ALERT_DOT_PX, _CHIP_ALERT_DOT_PX))
                x = placement.right
                continue
            if seg.kind == island_layout.KIND_PRIMARY:
                painter.setFont(self._font_for("primary"))
                painter.setPen(primary_text_color)
                value_color = primary_text_color
            else:
                if seg.label:
                    painter.setFont(self._font_for("label"))
                    painter.setPen(secondary_color)
                    painter.drawText(QPointF(placement.label_x, baseline), seg.label)
                painter.setFont(self._font_for("value"))
                # sampling/unknown/error 一律次色、不染告警色：胶囊上不做状态染色
                # （状态差异交给状态点与卡片），平衡"安静"与"诚实的 --"
                value_color = (primary_color if seg.state == island_segments.STATE_OK
                               else secondary_color)
                if seg.state == island_segments.STATE_OK:
                    # 告警只染**数值**（单位盒跟随值色，见下面的 _unit_color）：
                    # label / 段间竖线 / 段底一律不沾（裁决稿 §三）
                    alert_color = self._alert_color(seg.severity)
                    if alert_color is not None:
                        value_color = alert_color
                painter.setPen(value_color)
            # 盒内右对齐 + 统一基线（所有字号层共用一条基线，见 _BASELINE_DY）
            painter.drawText(QPointF(placement.text_x, baseline), placement.text)
            if placement.unit_text:
                # 单位盒：左对齐贴数值盒（+1px）、10px 层、值色 α×70%——只有"值盒
                # 右对齐 + 单位盒左对齐"才做得到 9%→100% 时个位不动（裁决稿 §二-5）
                painter.setFont(self._font_for("label"))
                painter.setPen(self._unit_color(value_color))
                painter.drawText(QPointF(placement.unit_x, baseline), placement.unit_text)
            x = placement.right
        if status:
            painter.setPen(Qt.PenStyle.NoPen)
            self._status_dot_rect = QRectF(
                x + _STATUS_GAP_PX, (_CAPSULE_HEIGHT - _STATUS_DOT_PX) / 2,
                _STATUS_DOT_PX, _STATUS_DOT_PX)
            self._paint_status_dot(painter, self._status_dot_rect)
        else:
            # 不画点就没有点位：声纳环以它为圆心，留着上一次的矩形就会绕空转
            self._status_dot_rect = None
        painter.end()
        self._content_pixmap = pm
        self._content_cache_key = key
        return pm

    def _paint_capsule(self, painter: QPainter) -> None:
        # 展开时先画卡片底板（胶囊下方一整块，圆角连续；内容区遵守同心圆角）
        if self._mode == "expanded":
            panel = QRectF(0, 0, self.width(), self.height())
            background, _p, _s = self._style_palette()
            # 卡片底板走可读性钳制（不随胶囊透明度降下去）
            background = self._readable_card_background(background)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(0, 0, 0, 40))
            painter.drawRoundedRect(panel.translated(0, 2), 18, 18)
            painter.setBrush(background)
            painter.drawRoundedRect(panel, 18, 18)
            painter.setPen(QPen(self._keyline_color(), 1))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(panel.adjusted(0.5, 0.5, -0.5, -0.5), 17.5, 17.5)

        # 胶囊外形：内缩 _CAPSULE_INSET + 果冻形变（只有外形参与 squish）
        rect, radius = self._squished_capsule_rect()

        # 柔和阴影：三层半透明圆角矩形（跟随形变）——自外向内收窄、只向下长，
        # 越往外越淡越大（见 _CAPSULE_SHADOW_LAYERS）
        if self._mode != "expanded":
            painter.setPen(Qt.PenStyle.NoPen)
            for inset, drop, alpha in _CAPSULE_SHADOW_LAYERS:
                painter.setBrush(QColor(0, 0, 0, alpha))
                painter.drawRoundedRect(
                    rect.adjusted(inset, 1.0, -inset, drop), radius, radius)

        # 胶囊主体（白色 / 黑色 / 玻璃质感）；内容层已下沉到 _content_cache，
        # 主/次文字色在这里不再使用
        background, _primary_color, _secondary_color = self._style_palette()
        painter.setBrush(background)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(rect, radius, radius)
        style = str(self._cfg.get("style") or "dark")
        if style == "glass":
            # 玻璃高光描边，强化“液化玻璃”边缘
            painter.setPen(QPen(QColor(255, 255, 255, 130), 1))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), radius - 0.5, radius - 0.5)
        else:
            # 深色底 key line：在暗壁纸上把轮廓勾出来
            painter.setPen(QPen(self._keyline_color(), 1))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), radius - 0.5, radius - 0.5)

        # 顶部光泽（sheen）：受光的一道淡白带，画在外壳之上、内容之下——每帧一笔、
        # 不进内容缓存（与 _paint_bevel 同口径）。裁剪到胶囊外形：光泽是"表面受光"，
        # 不能溢出轮廓（squish 鼓出时 rect 已变宽，裁剪切跟着变）。亮风 alpha=0 跳过。
        sheen_alpha = _SHEEN_ALPHA.get(style, 0)
        if sheen_alpha:
            sheen_rect = QRectF(
                rect.left(), rect.top() + _SHEEN_TOP_PX, rect.width(),
                rect.height() * _SHEEN_SPAN - _SHEEN_TOP_PX)
            sheen = QLinearGradient(0, sheen_rect.top(), 0, sheen_rect.bottom())
            sheen.setColorAt(0.0, QColor(255, 255, 255, sheen_alpha))
            sheen.setColorAt(1.0, QColor(255, 255, 255, 0))
            painter.save()
            clip = QPainterPath()
            clip.addRoundedRect(rect, radius, radius)
            painter.setClipPath(clip)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(sheen)
            painter.drawRect(sheen_rect)
            painter.restore()

        # 内容层（图标/名称/信息/状态灯）：缓存位图 blit——文字逐帧重画是
        # 实测大头（见 _content_cache）；内容绝不参与形变，口径与原"按未
        # 形变静止位绘制"一致。结构变化时旧位图渐出（判据见 _structure_key /
        # _sync_content_fade）。
        content = self._content_cache()
        self._sync_content_fade()
        outgoing = self._content_fade_from
        if outgoing is not None and self._content_fade_t < 1.0:
            # 单侧淡化（主代理裁决 2026-10-07 + astra M3 遮罩 2026-10-07）：
            # 旧位图**只在新内容透明的区域**按 (1-t) 渐出，新位图始终以 opacity 1.0
            # 直绘。两侧都按系数画会让共用区被混两次（α ≈ t+(1-t)²）；而"旧图整张垫
            # 底 + 新图盖面"对**半透明**共用像素同样不成立——合成 α = a+a(1-a)(1-t)，
            # 单位盒（α70%）会从 0.70 被抬到 0.91（实测公式，E3 的 hit=255 探针
            # 看不见它）。所以旧图先按「新内容的透明区」做遮罩：共用像素只剩新图
            # （任何 alpha 都逐帧恒定），旧结构独有残影照常渐隐。
            #
            # 遮罩按 (旧图, 新图) 对缓存：淡化中新内容因采样刷新重建时自动重算；
            # createMaskFromColor 只认 alpha=0，抗锯齿的半透边缘也算"有新墨"，
            # 旧图不会垫在它们底下。
            masked = self._fade_outgoing_masked
            masked_key = (outgoing.cacheKey(), content.cacheKey())
            if masked is None or self._fade_masked_key != masked_key:
                from PySide6.QtGui import QBitmap
                masked = QPixmap(outgoing)
                masked.setMask(QBitmap.fromImage(
                    content.toImage().createMaskFromColor(
                        0, Qt.MaskMode.MaskOutColor)))  # 0 = 全透明 QRgb
                # 本 Qt6 构建的 setMask 语义：掩码 1 位 = 透明（隔离探针实测），
                # 故用 MaskOutColor——位 1 落在「新内容有墨」处（=旧图那些像素
                # 变透明），位 0（不透明）恰好留在「新内容透明」的残影区。
                self._fade_outgoing_masked = masked
                self._fade_masked_key = masked_key
            painter.save()
            clip = QPainterPath()
            clip.addRoundedRect(rect, radius, radius)
            painter.setClipPath(clip)
            painter.setOpacity(1.0 - self._content_fade_t)
            painter.drawPixmap(0, 0, masked)
            painter.restore()
            painter.drawPixmap(0, 0, content)
        else:
            painter.drawPixmap(0, 0, content)
        self._content_drawn = content

        # 软陶笔（P2 Slice E3）：内缘 1px 高光/暗边，画在内容之上、跟随形变、
        # 不进内容缓存（每帧两笔固定成本，见 _paint_bevel）
        self._paint_bevel(painter, rect, radius)

        # 工作态声纳环（P3）：状态点外扩的 accent 圈，画在最上层——它是"当刻
        # 相位"的函数，逐帧重画是本质；内容缓存里没有它，缓存键自然不含它
        self._paint_sonar_ring(painter)

    def _paint_sonar_ring(self, painter: QPainter) -> None:
        """画工作态声纳环（非工作态/表未跑时一笔不画）。

        圆心取内容层画点时顺手记下的点位矩形（``_status_dot_rect``）——绘制层
        不自己重算落位，两处口径不可能分叉。相位按**真实时间**取（周期取模），
        tick 抖动只影响帧的疏密，不影响曲线位置。
        """
        dot = self._status_dot_rect
        if dot is None or self._sonar_timer is None:
            return
        phase = ((time.monotonic() - self._sonar_started) * 1000.0
                 % _SONAR_PERIOD_MS) / _SONAR_PERIOD_MS
        radius, alpha = sonar_ring_params(phase)
        if alpha <= 0:
            return
        color = self._accent_color()
        color.setAlpha(alpha)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(color, _SONAR_PEN_PX))
        painter.drawEllipse(dot.center(), radius, radius)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(Qt.BrushStyle.NoBrush)

    # ------------------------------------------------------------ 显隐休眠
    def hideEvent(self, event) -> None:  # noqa: N802
        """隐藏即休眠：信息轮询/动效/停靠收回/卡片自动收起/完成瞬态/声纳环
        全部停表，隐藏态零定时器空转。"""
        super().hideEvent(event)
        self._info_timer.stop()
        self._tier_tick_timer.stop()
        self._anim_timer.stop()
        self._dock_back_timer.stop()
        self._card_collapse_timer.stop()
        self._card_tick_timer.stop()
        self._stop_dot_done_timer()
        self._stop_sonar_ring()
        self._stop_content_fade()
        # 光标旗帜随隐藏复位（A1 第二票 R3）：隐藏期间光标真实位置不可知，
        # 不复位的话显示后 expand_card 会拿陈旧 True 误判「鼠标在岛内」不起 15s 表
        self._cursor_inside = False
        # 网络槽/设备槽同样休眠：采样线程与读取定时器一起停（探测在途最多 1s，
        # 不 join；订阅保留，显示后 _sync_*_slot 幂等恢复）
        self._pause_network_slot()
        self._pause_sys_slot()
        self._pause_extension_slot()
        self._pause_fps_slot()

    def changeEvent(self, event) -> None:  # noqa: N802
        """应用字体/DPI 变化：丢掉按字号层缓存的字体与度量（下次取值重建）。

        度量缓存按对象身份缓存，字体真的换了却还拿旧度量，盒宽与内容层位图就会
        继续用旧字号画——这是"字体指纹"白名单分量必须能变的前提。
        """
        super().changeEvent(event)
        if event is not None and event.type() == QEvent.Type.FontChange:
            self._clear_font_cache()
            # 卡片各区高度是按字体度量实测的：字号一变必须重算（否则行高、三列
            # 基线与卡片高都停在旧字号上；卡片只在展开态可见，所以只在展开态重排）
            if getattr(self, "_mode", "") == "expanded":
                self._sync_card_labels()
                self._resize_card_if_needed()
            elif not self._dragging:
                # 普通胶囊同样要把几何提交掉（R1 评审修复）：缓存作废后下一次绘制
                # 会按新字体重算计划，窗口宽若不同步就会与计划宽脱节（最长拖到
                # 30s 心跳）。拖拽中冻结是既有纪律，松手由 mouseReleaseEvent 重算。
                self._update_size()

    def _clear_font_cache(self) -> None:
        self._fonts.clear()
        self._font_metrics.clear()
        self._plan_inputs_key = None
        self._content_cache_key = None

    def closeEvent(self, event) -> None:  # noqa: N802
        """窗口销毁：两条采样器主动收口（ops5.5 评审 M1）。

        线程是 daemon、进程退时不阻塞，但 ``close()`` 是设计好的收口路径
        （``sys_monitor.close`` 有界 join、``net_monitor.close`` 非阻塞停），
        只在配置变更分支被调到的话，窗口销毁这条路就永远走不到——补上，
        与 ``pet/window.py`` 退出收口径一致。幂等：两处都判 None。
        注意 ``closeEvent`` 不发 ``stop()`` 只发 ``close()``：线程收到停止事件
        后自行退出（实测 ~3ms），daemon 也不阻塞进程退出——这里兑现的是
        「发出停止 + 订阅清理」，不是「返回前已 join」。
        """
        if self._net_monitor is not None:
            self._net_monitor.close()
            self._net_monitor = None
        if self._sys_monitor is not None:
            # 订阅账本先清零再收口：``close()`` 只负责停线程，退订是宿主的事——
            # 窗口要没了，订阅不该留在底座上（设备槽与 GPU 两本账一起清）。
            self._unsubscribe_sys()
            self._sys_monitor.close()
            self._sys_monitor = None
        if self._ext_sampler is not None:
            # 第三方段同样：订阅清零 + 收口（采样线程是 daemon，不阻塞进程退出）
            self._unsubscribe_extensions()
            self._ext_sampler.close()
            self._ext_sampler = None
        # 第三方段的表也要停：窗口关了就没有"下一次读快照"，留着只是一条空转的定时器
        # （``close()`` 只负责停线程与退订，停表是宿主的事，与 hideEvent 同口径）。
        self._ext_timer.stop()
        # 帧率段：停表 + 退订 + 丢这一岛对共享采集器的引用（**不 join**：收口的
        # 有界等待在采集线程自己那边，GUI 线程上等它就是"关个岛卡三秒"）。还有别的
        # 岛在看读数时，这一岛的销毁不会把采集链路带走（宿主按岛计数）。
        self._release_fps_slot()
        super().closeEvent(event)

    def showEvent(self, event) -> None:  # noqa: N802
        """重新显示：恢复信息轮询并立即刷新（沉睡期间的内容变化补上），
        几何直接归位（隐藏可能打断了停靠/展开动画）。"""
        super().showEvent(event)
        self._finish_animations()
        self._info_timer.start()
        if self._mode == "expanded":
            # 隐藏期间卡片还开着（hideEvent 停了每秒 tick）：显示回来就接上，
            # 否则"更新时间"会冻在隐藏那一刻
            self._card_tick_timer.start()
            self._sync_card_labels()
            # 自动收起倒计时同样接回（S2 评审修复：hideEvent 停了它就再也不起，
            # 卡片会永远开着）；光标在岛内则维持 hover 暂停，与 expand 同口径
            if not self._cursor_inside:
                self._card_collapse_timer.start()
        self._schedule_next_balance_tier_refresh()
        # 隐藏期间可能装/卸了组件：显示回来重扫一次清单再恢复采样
        self._refresh_extension_declarations()
        self._sync_network_slot()
        self._sync_sys_slot()
        self._sync_extension_slot()
        self._sync_fps_slot()
        # 隐藏期间可能已经进入工作态（hideEvent 停了环）：显示回来按当刻判据重判
        # ——"工作中"就该有声纳，上升沿早过去了不构成不播的理由（见 _sync_sonar_ring）
        self._sync_sonar_ring()
        self._refresh()
        # 原生窗口此刻已就绪；再延迟一拍重申一次 topmost——show 期间 Qt 还在
        # 收尾窗口排序，它可能覆盖同步那次（口径同 legacy window.py showEvent）
        QTimer.singleShot(0, self, self.reassert_topmost)

    # ------------------------------------------------------------ 悬停
    def _hover_rects(self) -> tuple[QRect, ...]:
        """可 hover 区的矩形集合 = 当前窗口矩形 + 细条矩形；非停靠态只有窗口矩形。

        左右停靠时滑出胶囊比细条矮：细条 16×64、胶囊高 44 且以细条中心居中，细条
        上下各露 (64-44)/2 = 10px **死区**——光标停在那里时滑出的窗口盖不住它
        （Qt 合成 leave），但收回细条又会盖住它（enter），于是形成
        收回 → 再滑出 的自持循环。所以"鼠标走了没有"不能只看窗口矩形，要看这张网。

        细条矩形按当前窗口推导（``strip_rect_for``），与 ``_target_rect`` 的停靠
        收口公式同源：滑出时它就是收回后窗口将落到的那个矩形（有测试钉住这条等价）。
        **逐矩形判包含**（``any(contains)``），不取 ``QRect.united`` 的包围盒——包围
        盒会把"胶囊下缘外那 10px 横带"也划进区内（那里的像素既不在胶囊里也不在细条
        里，收回去完全安全），收回会被无谓地拖到光标走得更远才发生。
        """
        rect = self.geometry()
        if self._mode != "docked":
            return (rect,)
        screen = self._current_screen()
        if screen is None:
            return (rect,)
        strip = strip_rect_for(rect, self.dock_edge, screen.availableGeometry())
        return (rect,) if strip == rect else (rect, strip)

    def _cursor_in_hover_zone(self) -> bool:
        """光标是否仍在可 hover 区内（``_hover_rects`` 的唯一判据入口）。"""
        point = QCursor.pos()
        return any(rect.contains(point) for rect in self._hover_rects())

    def enterEvent(self, event) -> None:  # noqa: N802
        self._cursor_inside = True
        # 展开卡片时不悬停放大（整窗缩放会截掉贴边的卡片底板，见 paintEvent）
        self._hover_scale_target = 1.0 if self._mode == "expanded" else 1.02
        if self._mode == "expanded":
            # hover 暂停自动收起（裁决稿 §一-10：用户在读卡片/准备点按钮时
            # 不许被 15s 定时器收掉；离开即重新倒计时）
            self._card_collapse_timer.stop()
        if self._mode == "docked" and not self._hover_peek:
            self._dock_back_timer.stop()
            self._hover_peek = True
            self._animate_to(self._target_rect())
            self._sync_content_sampling()   # 滑出 = 内容重新可见 → 恢复采样
        self._ensure_anim_timer()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._cursor_inside = False
        self._hover_scale_target = 1.0
        if self._mode == "expanded":
            self._card_collapse_timer.start()
        if self._mode == "docked" and self._hover_peek:
            # 这里**不**按可 hover 区拦表：只离开窗口、没离开区（死区）时仍然起表，
            # 表到点由 _dock_back 按同一判据复检，光标真走了才会收回。在这里拦会
            # 漏掉收口——光标先停在死区（leave 发生在区内、被拦下）再走出细条时，
            # 窗口和光标都不再动，不会再有第二个事件把它找回来，滑出就永久钉住
            # （实机实测：真实光标停在死区、随后移出细条，滑出 3s 不回收）。
            self._dock_back_timer.start()
        self._ensure_anim_timer()
        super().leaveEvent(event)

    def _dock_back(self) -> None:
        if self._mode == "docked" and self._hover_peek:
            if self._cursor_in_hover_zone():
                # 复检：细条单击滑出的预览（光标就停在细条上）、以及"光标还泡在
                # 死区里"这两种情况都会走到这里。此刻收回 → 细条立刻盖回光标 →
                # enterEvent 再滑出，就是 1s 周期的自持振荡。不收回，也不留悬表：
                # 延后一拍再判，光标真走了仍会在一个周期（800ms）内收口。
                self._dock_back_timer.start()
                return
            self._hover_peek = False
            self._animate_to(self._target_rect())
            self._sync_content_sampling()   # 收回细条 = 内容全不画 → 全停采样

    # ------------------------------------------------------------ 鼠标
    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            # 点岛 = 用户明确要它在前（legacy「点击把窗口带回置顶组最前」口径，
            # 只改 z 序不抢键盘焦点）
            self.reassert_topmost()
            self._press_global = event.globalPosition().toPoint()
            self._drag_offset = self._press_global - self.pos()
            self._dragging = False
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._press_global is None or event.buttons() & Qt.MouseButton.LeftButton == 0:
            return
        global_pos = event.globalPosition().toPoint()
        if not self._dragging and (global_pos - self._press_global).manhattanLength() >= QApplication.startDragDistance():
            self._dragging = True
            # 起拖即停进行中的内容淡化（R1 评审修复：`_dragging` 只拦新淡化，
            # 不停已在播的——"先淡化、后起拖"会让残影在拖拽中继续推进 130ms，
            # 违反"拖拽中不播"）；停表即跳终点帧，口径与 hideEvent 一致
            self._stop_content_fade()
            # 起拖先清几何插值状态（滑出/停靠/归位动画的目标）：否则动画
            # 每 16ms 把窗口拽回目标位，与拖拽 move() 互抢（不跟手/60Hz
            # 抖动，且期间碰撞结算被 _geo_to 守卫全程跳过）。注意不停动画
            # 表：_on_anim_tick 只在 _geo_to 非 None 时插值几何，停表会把
            # squish/kick 弹簧冻在中途
            self._geo_from = None
            self._geo_to = None
            if self._mode == "expanded":
                self.collapse_card(animate=False)  # 拖拽起手直接落位，不播收起动画
            if self._mode == "docked":
                # 拖出停靠：进入正常模式跟随光标
                self._mode = "normal"
                self._hover_peek = False
                self._apply_fixed_size()
                self._sync_content_sampling()   # 拖出细条 = 内容重新可见 → 恢复采样
        if self._dragging and self._drag_offset is not None:
            self.move(global_pos - self._drag_offset)
            self._emit_geometry_changed()
            event.accept()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton:
            return
        # 选屏按**松手光标**的全局位置，不按窗口左上角：跨屏拖拽的触发带恰等于
        # 抓取偏移量——光标刚过屏缝松手时左上角仍在原屏，按左上角选屏会把整窗
        # 夹回原屏边缘（夹取距离 0 → 就近判定停靠）并落回原屏，用户看到的是
        # "拖过去又弹回来"（issue #154）。光标在哪块屏，岛就落在哪块屏。
        # 心跳 _clamp_to_screen 仍按窗口左上角：跨缝中途夹回所在屏是保守合理
        # 行为，不动。
        screen = self._screen_at(event.globalPosition().toPoint())
        available = screen.availableGeometry() if screen is not None else None
        if not self._dragging:
            if self._mode == "docked" and not self._hover_peek:
                # 细条单击 = 滑出预览（不停留展开卡片，保持轻量）
                self._hover_peek = True
                self._animate_to(self._target_rect())
                self._dock_back_timer.start()
            elif self._click_action() == "toggle_pet":
                self.clicked.emit()
            elif self._hidden_chat_enabled() and not self._pet_visible and self._chat_available:
                # 桌宠隐藏时岛是唯一常驻交互面：单击直接弹对话气泡
                #（恢复桌宠的入口由气泡内的「显示桌宠」按钮承接）；
                # 无聊天能力的构建（纯桌宠版）不进此分支——回退 else 展开
                # 卡片，由卡片的「显示桌宠」按钮承接恢复入口
                self.chat_requested.emit()
            else:
                self.expand_card()
        else:
            # 先夹回屏幕再判定停靠：拖出屏外的落点按夹后位置算距离
            if available is not None:
                x = max(available.left(), min(self.x(), available.right() - self.width() + 1))
                y = max(available.top(), min(self.y(), available.bottom() - self.height() + 1))
                self.move(x, y)
            edge = "none"
            if available is not None and self._edge_dock_enabled():
                edge = dock_edge_for(self.geometry(), available)
                rect = self.geometry()
                logger.info(
                    "灵动岛拖拽落点 (%d,%d)：距四边 top=%d bottom=%d left=%d right=%d"
                    "（阈值 %d）→ dock=%s",
                    rect.x(), rect.y(),
                    abs(rect.top() - available.top()),
                    abs(available.bottom() - rect.bottom()),
                    abs(rect.left() - available.left()),
                    abs(available.right() - rect.right()),
                    _DOCK_THRESHOLD, edge)
            # 松手落定：解除拖拽冻结并重算几何——跨屏迁移后收缩级按**新屏**重算
            #（拖拽过程中刻意冻结，见 _update_size）。放在停靠判定之后：判定看的是
            # 用户挑的那个落点，不该被"刚落定的收缩"改判。标志先翻再动几何，与桥的
            # "松手先翻标志、再发几何回调"口径一致（_emit_geometry_changed 在尾）
            self._dragging = False
            self._update_size()
            self._save_dock_edge(edge)
            self._save_position()
            if edge != "none":
                self._mode = "docked"
                self._hover_peek = False
                self._animate_to(self._target_rect())
            elif self._mode == "docked":
                self._mode = "normal"
                self._animate_to(self._target_rect())
        self._press_global = None
        self._drag_offset = None
        self._dragging = False
        # 分支决定完模式再同步采样：细条单击 → 滑出（恢复）、拖拽落定 → 停靠（全停）
        self._sync_content_sampling()
        self._sync_sonar_ring()          # 拖拽落定可能停靠（细条不播环）
        self._emit_geometry_changed()
        event.accept()

    # ------------------------------------------------------------ 测试钩子
    def _debug_state(self) -> dict:
        """测试用快照：模式/停靠边/几何动画是否活跃。"""
        return {
            "mode": self._mode,
            "dock_edge": self.dock_edge,
            "hover_peek": self._hover_peek,
            "animating": self._anim_timer.isActive(),
            "card_visible": self._card_box.isVisible(),
        }
